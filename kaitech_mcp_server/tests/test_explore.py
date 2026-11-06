# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import time

import odoo
from odoo.modules import module as odoo_module

from .. import explore, explore_tools
from .common import EndpointCase, new_key, new_user

EXPLORE_TOOLS = ('describe_model', 'search_methods', 'describe_method', 'get_method_source', 'search_code',
                 'read_source', 'list_modules', 'describe_module', 'get_view', 'list_actions', 'search_xmlids')


class TestExplore(EndpointCase):

    def setUp(self):
        super(TestExplore, self).setUp()
        with self.shared_env() as env:
            user = new_user(env, 'mcp_explore')
            key, self.token = new_key(env, user, {'res.partner': {'read'}}, allow_explore=True)
            self.key_id = key.id
            _plain, self.plain_token = new_key(env, user, {'res.partner': {'read'}}, name='plain client')
            self.company_id = env.ref('base.main_company').id

    def structured(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertFalse(result['isError'], result)
        return result['structuredContent']

    def refused(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertTrue(result['isError'], result)
        return result['structuredContent']['error']

    def test_explore_needs_the_key_switch(self):
        listed = [tool['name'] for tool in self.legacy('tools/list', token=self.plain_token)[1]['result']['tools']]
        self.assertFalse(set(EXPLORE_TOOLS) & set(listed))
        self.assertEqual(self.refused('describe_model', {'model': 'res.partner'}, token=self.plain_token),
                         'There is no tool named describe_model.')
        self.assertIn('Unexpected arguments', self.refused('list_models', {'all': True}, token=self.plain_token))
        tools = self.legacy('tools/list', token=self.token)[1]['result']['tools']
        self.assertEqual([name for name in [tool['name'] for tool in tools] if name in EXPLORE_TOOLS], list(EXPLORE_TOOLS))
        listing = [tool for tool in tools if tool['name'] == 'list_models'][0]
        self.assertTrue(set(['all', 'field', 'offset']) <= set(listing['inputSchema']['properties']))

    def test_initialize_teaches_the_exploring_order_only_to_explore_keys(self):
        plain = self.legacy('initialize', {'protocolVersion': '2025-06-18'}, token=self.plain_token)[1]['result']
        explore = self.legacy('initialize', {'protocolVersion': '2025-06-18'}, token=self.token)[1]['result']
        self.assertNotIn('describe_model', plain['instructions'])
        self.assertNotIn('resources', plain['capabilities'])
        self.assertIn('describe_model', explore['instructions'])
        self.assertLess(explore['instructions'].index('describe_model'), explore['instructions'].index('get_method_source'))
        self.assertIn('resources', explore['capabilities'])

    def test_overview_resource(self):
        listed = self.legacy('resources/list', token=self.token)[1]['result']['resources']
        self.assertEqual([item['uri'] for item in listed], ['odoo://overview'])
        text = self.legacy('resources/read', {'uri': 'odoo://overview'}, token=self.token)[1]['result']['contents'][0]['text']
        self.assertIn('# Odoo %s' % odoo.release.version, text)
        self.assertIn('kaitech_mcp_server', text)
        self.assertEqual(self.legacy('resources/read', {'uri': 'file:///etc/passwd'}, token=self.token)[1]['error']['code'],
                         -32002)
        self.assertEqual(self.legacy('resources/list', token=self.plain_token)[1]['error']['code'], -32601)

    def test_list_models_all_lists_every_model_with_its_module(self):
        found = self.structured('list_models', {'all': True, 'query': 'kaitech.mcp.key', 'limit': 100})
        self.assertEqual(found['columns'], ['model', 'description', 'module', 'kind'])
        rows = dict((row[0], row) for row in found['models'])
        self.assertEqual((rows['kaitech.mcp.key'][2], rows['kaitech.mcp.key'][3]), ('kaitech_mcp_server', 'model'))
        self.assertEqual(rows['kaitech.mcp.key.wizard'][3], 'wizard')
        self.assertEqual(found['total'], len(found['models']))
        first = self.structured('list_models', {'all': True, 'limit': 3})
        self.assertTrue(first['has_more'])
        second = self.structured('list_models', {'all': True, 'limit': 3, 'offset': first['next_offset']})
        self.assertFalse(set(row[0] for row in first['models']) & set(row[0] for row in second['models']))

    def test_list_models_all_with_a_field_shows_its_type_and_target(self):
        found = self.structured('list_models', {'all': True, 'field': 'partner_id', 'query': 'res.users'})
        self.assertEqual(found['columns'][-1], 'field')
        self.assertEqual([(row[0], row[-1]) for row in found['models']], [('res.users', 'many2one(res.partner)')])
        none = self.structured('list_models', {'all': True, 'field': 'partner_id', 'query': 'kaitech.mcp.key'})
        self.assertEqual((none['models'], none['total']), ([], 0))
        plain_field = self.structured('list_models', {'all': True, 'field': 'is_company', 'query': 'res.partner'})
        self.assertIn(['res.partner', 'boolean'], [[row[0], row[-1]] for row in plain_field['models']])
        self.assertIn('field works only with all', self.refused('list_models', {'field': 'name'}))
        self.assertIn('all must be true or false', self.refused('list_models', {'all': 'yes'}))
        self.assertIn('field must be a field name', self.refused('list_models', {'all': True, 'field': 5}))

    def test_describe_model(self):
        described = self.structured('describe_model', {'model': 'res.partner'})
        self.assertEqual(described['module'], 'base')
        self.assertEqual(described['fields']['parent_id']['relation'], 'res.partner')
        self.assertEqual(described['fields']['commercial_partner_id']['compute'], '_compute_commercial_partner')
        self.assertIn('is_company', described['fields']['commercial_partner_id']['depends'])
        self.assertIn('create_company', described['methods'])
        self.assertEqual(described['can'], {'read': True, 'create': False, 'update': False, 'delete': False,
                                            'call': False})
        only = self.structured('describe_model', {'model': 'res.partner', 'fields': ['name', 'nope']})
        self.assertEqual((sorted(only['fields']), only['unknown_fields']), (['name'], ['nope']))
        self.assertIn('Unknown model', self.refused('describe_model', {'model': 'no.such.model'}))
        named = self.structured('describe_model', {'model': 'res.partner', 'query': 'commercial'})
        self.assertIn('commercial_partner_id', named['fields'])
        self.assertTrue(all('commercial' in name for name in named['fields']))
        self.assertIn('_commercial_fields', named['methods'])
        self.assertTrue(all('commercial' in name for name in named['methods']))

    def test_method_override_order_and_source(self):
        found = self.structured('search_methods', {'query': 'create_compan'})
        self.assertIn({'model': 'res.partner', 'method': 'create_company', 'modules': ['base']}, found['methods'])
        described = self.structured('describe_method', {'model': 'res.users', 'method': 'write'})
        overrides = described['overrides']
        self.assertGreaterEqual(len(overrides), 3)
        self.assertIn('base/res/res_users.py', [item['file'] for item in overrides])
        self.assertEqual(overrides[-1]['module'], 'base')
        self.assertTrue(overrides[0]['calls_super'])
        in_base = [item['line'] for item in overrides if item['file'] == 'base/res/res_users.py']
        self.assertEqual(in_base, sorted(in_base, reverse=True))
        self.assertEqual(described['framework']['file'], 'odoo/models.py')
        self.assertTrue(described['signature'].startswith('write(self'))
        computed = self.structured('describe_method', {'model': 'res.partner', 'method': '_compute_commercial_partner'})
        self.assertEqual(computed['computes'], ['commercial_partner_id'])
        self.assertIn('api.depends(', ' '.join(computed['decorators']))
        source = self.structured('get_method_source', {'model': 'res.users', 'method': 'write'})
        self.assertEqual(len(source['overrides']), len(overrides))
        self.assertTrue(all('def write(self' in item['source'] for item in source['overrides']))
        only = self.structured('get_method_source', {'model': 'res.users', 'method': 'write', 'module': 'base'})
        self.assertTrue(only['overrides'])
        self.assertEqual(set(item['module'] for item in only['overrides']), set(['base']))
        self.assertIn('no definition in module', self.refused('get_method_source', {
            'model': 'res.users', 'method': 'write', 'module': 'kaitech_mcp_server'}))
        short = self.structured('get_method_source', {'model': 'res.users', 'method': 'write', 'max_lines': 3})
        self.assertTrue(short['truncated'])
        self.assertEqual(sum(item['source'].count('\n') for item in short['overrides'] if item.get('source')), 3)
        self.assertIn('has no method', self.refused('describe_method', {'model': 'res.partner', 'method': 'nope'}))

    def test_search_code_and_read_source(self):
        found = self.structured('search_code', {'query': 'def create_company', 'module': 'base'})
        hit = [row for row in found['matches'] if row['file'] == 'base/res/res_partner.py'][0]
        self.assertEqual(hit['text'], 'def create_company(self):')
        read = self.structured('read_source', {'path': hit['file'], 'start_line': hit['line'], 'lines': 2})
        self.assertTrue(read['text'].startswith('%d: ' % hit['line']))
        self.assertIn('def create_company(self):', read['text'])
        self.assertEqual(read['next_start_line'], hit['line'] + 2)
        narrowed = self.structured('search_code', {'query': 'create_company', 'path': '*/tests/*'})
        self.assertTrue(all('/tests/' in row['file'] for row in narrowed['matches']))
        paged = self.structured('search_code', {'query': 'import', 'module': 'base', 'limit': 2})
        self.assertTrue(paged['has_more'])
        after = self.structured('search_code', {'query': 'import', 'module': 'base', 'limit': 2, 'offset': 2})
        self.assertNotEqual(paged['matches'], after['matches'])

    def test_search_code_regex_and_case(self):
        pattern = r'def create_compan[a-z]\(self\)'
        regexed = self.structured('search_code', {'query': pattern, 'module': 'base', 'regex': True})
        self.assertEqual(regexed['matches'][0]['text'], 'def create_company(self):')
        literal = self.structured('search_code', {'query': pattern, 'module': 'base'})
        self.assertEqual(literal['matches'], [])
        shouted = {'query': 'DEF CREATE_COMPANY', 'module': 'base'}
        self.assertEqual(self.structured('search_code', shouted)['matches'], [])
        folded = self.structured('search_code', dict(shouted, ignore_case=True))
        self.assertIn('def create_company(self):', [row['text'] for row in folded['matches']])
        self.assertIn('Invalid regex', self.refused('search_code', {'query': '(unclosed', 'regex': True}))
        self.assertIn('at most 200', self.refused('search_code', {'query': 'a' * 201, 'regex': True}))
        self.assertIn('regex must be true or false', self.refused('search_code', {'query': 'import', 'regex': 'yes'}))
        js = self.structured('search_code', {'query': 'odoo.define', 'module': 'web', 'file_types': ['js'], 'limit': 1})
        self.assertTrue(js['matches'][0]['file'].endswith('.js'))

    def test_regex_search_is_linear_and_bounded(self):
        started = time.time()
        nested = self.structured('search_code', {'query': '(.+)+Z_NO_SUCH_END', 'module': 'base', 'file_types': ['py'],
                                                 'regex': True})
        self.assertEqual(nested['matches'], [])
        self.assertLess(time.time() - started, 20)
        self.assertIn('Backreferences', self.refused('search_code', {'query': '(a)\\1', 'regex': True}))
        self.assertIn('one line', self.refused('search_code', {'query': 'a\nb', 'regex': True}))
        found = self.structured('search_code', {'query': 'def create_compan[a-z]\\(self\\)', 'module': 'base',
                                                'regex': True, 'path': 'base/res/*'})
        self.assertEqual([row['file'] for row in found['matches']], ['base/res/res_partner.py'])

    def test_a_regex_search_that_runs_too_long_is_stopped(self):
        real_popen = explore_tools.subprocess.Popen
        limit = explore_tools.SEARCH_SECONDS

        def slow(command, **options):
            if command[:2] == ['grep', '-rIn']:
                return real_popen(['sleep', '30'], **options)
            return real_popen(command, **options)

        explore_tools.SEARCH_SECONDS = 0.3
        explore_tools.subprocess.Popen = slow
        try:
            started = time.time()
            result = self.structured('search_code', {'query': 'import', 'module': 'base', 'regex': True})
        finally:
            explore_tools.subprocess.Popen = real_popen
            explore_tools.SEARCH_SECONDS = limit
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['matches'], [])
        self.assertLess(time.time() - started, 10)

    def test_a_grep_hit_outside_the_shown_files_is_dropped(self):
        folder = os.path.realpath(tempfile.mkdtemp())
        try:
            module = os.path.join(folder, 'mod')
            for relative in ('a.py', '.hidden/b.py', 'lib/c.py', 'x.min.js', 'sub/d.py'):
                path = os.path.join(module, relative)
                if not os.path.isdir(os.path.dirname(path)):
                    os.makedirs(os.path.dirname(path))
                with open(path, 'w') as handle:
                    handle.write('x = 1\n')
            outside = os.path.join(folder, 'outside.py')
            with open(outside, 'w') as handle:
                handle.write('x = 1\n')
            os.symlink(outside, os.path.join(module, 'link.py'))
            roots = {module: 'mod'}
            shown = dict((relative, explore_tools._shown(os.path.join(module, relative), roots, {}))
                         for relative in ('a.py', '.hidden/b.py', 'lib/c.py', 'x.min.js', 'sub/d.py', 'link.py'))
        finally:
            shutil.rmtree(folder)
        self.assertEqual(shown, {'a.py': 'mod/a.py', 'sub/d.py': 'mod/sub/d.py', '.hidden/b.py': None, 'lib/c.py': None,
                                 'x.min.js': None, 'link.py': None})

    def test_a_source_file_is_read_without_following_a_symlink(self):
        folder = tempfile.mkdtemp()
        try:
            target = os.path.join(folder, 'target.py')
            with open(target, 'w') as handle:
                handle.write('x = 1\n')
            link = os.path.join(folder, 'link.py')
            os.symlink(target, link)
            plain = explore.read_text(target)
            followed = explore.read_text(link)
        finally:
            shutil.rmtree(folder)
        self.assertEqual(plain, u'x = 1\n')
        self.assertIsNone(followed)

    def test_read_source_stays_inside_installed_modules(self):
        refused = ['../../../etc/passwd', '/etc/passwd', 'base/../../release.py', 'base/../../../odoo/release.py',
                   'base/res/res_partner.pyc', 'base', 'web/static/../../base/res', 'no_such_module/__init__.py']
        if odoo.tools.config.rcfile:
            refused.append(odoo.tools.config.rcfile)
        uninstalled = sorted(set(odoo_module.get_modules()) - self.registry._init_modules)
        if uninstalled:
            refused.append('%s/__manifest__.py' % uninstalled[0])
        for path in refused:
            self.assertIn('Not a source file', self.refused('read_source', {'path': path}), path)
        self.assertIn('Not installed here', self.refused('search_code', {'query': 'import', 'module': 'no_such_module'}))

    def test_modules(self):
        listed = self.structured('list_modules', {'query': 'kaitech'})
        self.assertEqual([row['name'] for row in listed['modules']], ['kaitech_mcp_server'])
        self.assertNotIn('state', listed['modules'][0])
        waiting = self.structured('list_modules', {'state': 'uninstalled', 'limit': 500})
        self.assertTrue(waiting['modules'])
        self.assertTrue(all(row['state'] == 'uninstalled' for row in waiting['modules']))
        self.assertNotIn('kaitech_mcp_server', [row['name'] for row in waiting['modules']])
        everything = self.structured('list_modules', {'state': 'all', 'query': 'kaitech_mcp', 'limit': 500})
        self.assertEqual([(row['name'], row['state']) for row in everything['modules']],
                         [('kaitech_mcp_server', 'installed')])
        self.assertIn('state must be one of', self.refused('list_modules', {'state': 'nope'}))
        described = self.structured('describe_module', {'module': 'kaitech_mcp_server'})
        self.assertEqual(described['depends'], ['base', 'web'])
        self.assertIn('kaitech.mcp.key', described['models_defined'])
        self.assertIn('kaitech.mcp.key.wizard', described['wizards_defined'])
        self.assertIn('kaitech_mcp_server.view_mcp_key_form', described['views'])
        self.assertTrue(described['menus'])
        self.assertIn('web', self.structured('describe_module', {'module': 'base'})['dependents'])

    def test_view_actions_and_xmlids(self):
        view = self.structured('get_view', {'model': 'res.partner', 'view_type': 'form'})
        self.assertEqual(view['xmlid'], 'base.view_partner_form')
        self.assertTrue(view['arch'].startswith('<form'))
        by_id = self.structured('get_view', {'model': 'kaitech.mcp.key', 'view': 'kaitech_mcp_server.view_mcp_key_form',
                                             'lines': 5})
        self.assertEqual((by_id['end_line'], by_id['next_start_line']), (5, 6))
        by_number = self.structured('get_view', {'model': 'res.partner', 'view': view['view_id'], 'lines': 3})
        self.assertEqual(by_number['xmlid'], 'base.view_partner_form')
        listed = self.structured('get_view', {'model': 'res.partner', 'view_type': 'list', 'lines': 2})
        self.assertEqual(listed['view_type'], 'tree')
        self.assertTrue(listed['arch'].startswith('<tree'))
        self.assertIn('not the XML id of a view', self.refused('get_view', {'model': 'res.partner',
                                                                             'view': 'base.main_company'}))
        self.assertIn('No view with id', self.refused('get_view', {'model': 'res.partner', 'view': 999999999}))
        self.assertIn('view must be', self.refused('get_view', {'model': 'res.partner', 'view': True}))
        actions = self.structured('list_actions', {'model': 'kaitech.mcp.key'})
        self.assertIn('kaitech_mcp_server.action_mcp_key', [row['xmlid'] for row in actions['window_actions']])
        company = self.structured('search_xmlids', {'query': 'base.main_company'})['xmlids'][0]
        self.assertEqual((company['model'], company['res_id']), ('res.company', self.company_id))
        self.assertNotIn('display_name', company)
        partner = self.structured('search_xmlids', {'query': 'base.main_partner'})['xmlids'][0]
        self.assertIn('display_name', partner)
        reverse = self.structured('search_xmlids', {'model': 'res.company', 'res_id': self.company_id})
        self.assertIn('base.main_company', [row['xmlid'] for row in reverse['xmlids']])
        searched = self.structured('search_xmlids', {'query': 'base.main_comp'})
        self.assertIn('base.main_company', [row['xmlid'] for row in searched['xmlids']])
        narrowed = self.structured('search_xmlids', {'query': 'main_', 'model': 'res.company', 'limit': 200})
        self.assertTrue(narrowed['xmlids'])
        self.assertEqual(set(row['model'] for row in narrowed['xmlids']), set(['res.company']))
        self.assertIn('Give query', self.refused('search_xmlids', {}))
        self.assertIn('res_id needs model', self.refused('search_xmlids', {'res_id': 1}))

    def test_explore_calls_are_audited(self):
        self.structured('describe_model', {'model': 'res.partner'})
        call = self.last_call(self.key_id)
        self.assertEqual((call['result'], call['tool'], call['model']), ('ok', 'describe_model', 'res.partner'))
