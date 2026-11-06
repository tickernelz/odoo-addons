# -*- coding: utf-8 -*-
import odoo
from odoo.modules import module as odoo_module

from .common import EndpointCase, new_key, new_user

EXPLORE_TOOLS = ('describe_model', 'search_fields', 'search_methods', 'describe_method', 'get_method_source',
                 'search_code', 'read_source', 'list_modules', 'describe_module', 'get_view', 'list_actions',
                 'lookup_xmlid')


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
        self.assertIn('Unexpected arguments', self.refused('list_models', {'scope': 'all'}, token=self.plain_token))
        tools = self.legacy('tools/list', token=self.token)[1]['result']['tools']
        self.assertEqual([name for name in [tool['name'] for tool in tools] if name in EXPLORE_TOOLS], list(EXPLORE_TOOLS))
        listing = [tool for tool in tools if tool['name'] == 'list_models'][0]
        self.assertIn('scope', listing['inputSchema']['properties'])

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

    def test_list_models_scope_all_lists_every_model_with_its_module(self):
        found = self.structured('list_models', {'scope': 'all', 'query': 'kaitech.mcp.key', 'limit': 100})
        models = dict((row['model'], row) for row in found['models'])
        self.assertEqual(models['kaitech.mcp.key']['module'], 'kaitech_mcp_server')
        self.assertEqual(models['kaitech.mcp.key.wizard']['kind'], 'wizard')
        first = self.structured('list_models', {'scope': 'all', 'limit': 3})
        self.assertTrue(first['has_more'])
        second = self.structured('list_models', {'scope': 'all', 'limit': 3, 'offset': first['next_offset']})
        self.assertFalse(set(row['model'] for row in first['models']) & set(row['model'] for row in second['models']))

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

    def test_search_fields_finds_models_by_field_and_by_relation(self):
        found = self.structured('search_fields', {'query': 'partner_id', 'exact': True, 'limit': 200})
        rows = dict((row['model'], row) for row in found['fields'])
        self.assertEqual(found['total'], len(found['fields']))
        self.assertTrue(all(row['field'] == 'partner_id' for row in found['fields']))
        self.assertEqual((rows['res.users']['type'], rows['res.users']['relation']), ('many2one', 'res.partner'))
        pointing = self.structured('search_fields', {'relation': 'res.partner', 'type': 'many2one', 'query': 'parent'})
        self.assertIn(('res.partner', 'parent_id'), [(row['model'], row['field']) for row in pointing['fields']])
        self.assertTrue(all(row['relation'] == 'res.partner' and row['type'] == 'many2one'
                            for row in pointing['fields']))
        loose = self.structured('search_fields', {'query': 'partner_id', 'limit': 3})
        self.assertGreater(loose['total'], found['total'])
        self.assertEqual((loose['returned'], loose['has_more'], loose['next_offset']), (3, True, 3))
        second = self.structured('search_fields', {'query': 'partner_id', 'limit': 3, 'offset': 3})
        self.assertFalse(set((row['model'], row['field']) for row in loose['fields'])
                         & set((row['model'], row['field']) for row in second['fields']))
        self.assertIn('Give query', self.refused('search_fields', {}))
        self.assertIn('type must be one of', self.refused('search_fields', {'type': 'nope'}))
        self.assertIn('exact must be true or false', self.refused('search_fields', {'query': 'id', 'exact': 'yes'}))

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
        short = self.structured('get_method_source', {'model': 'res.users', 'method': 'write', 'max_lines': 3})
        self.assertTrue(short['truncated'])
        self.assertEqual(sum(item['source'].count('\n') for item in short['overrides'] if item.get('source')), 3)
        self.assertIn('has no method', self.refused('describe_method', {'model': 'res.partner', 'method': 'nope'}))

    def test_search_code_and_read_source(self):
        found = self.structured('search_code', {'query': 'def create_company', 'modules': ['base']})
        hit = [row for row in found['matches'] if row['file'] == 'base/res/res_partner.py'][0]
        self.assertEqual(hit['text'], 'def create_company(self):')
        read = self.structured('read_source', {'path': hit['file'], 'start_line': hit['line'], 'line_count': 2})
        self.assertTrue(read['text'].startswith('%d: ' % hit['line']))
        self.assertIn('def create_company(self):', read['text'])
        self.assertEqual(read['next_start_line'], hit['line'] + 2)
        narrowed = self.structured('search_code', {'query': 'create_company', 'path': '*/tests/*'})
        self.assertTrue(all('/tests/' in row['file'] for row in narrowed['matches']))
        paged = self.structured('search_code', {'query': 'import', 'modules': ['base'], 'limit': 2})
        self.assertTrue(paged['has_more'])
        after = self.structured('search_code', {'query': 'import', 'modules': ['base'], 'limit': 2, 'offset': 2})
        self.assertNotEqual(paged['matches'], after['matches'])

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
        self.assertIn('Not installed here', self.refused('search_code', {'query': 'import', 'modules': ['no_such_module']}))

    def test_modules(self):
        listed = self.structured('list_modules', {'query': 'kaitech'})
        self.assertEqual([row['name'] for row in listed['modules']], ['kaitech_mcp_server'])
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
        by_id = self.structured('get_view', {'model': 'kaitech.mcp.key', 'xmlid': 'kaitech_mcp_server.view_mcp_key_form',
                                             'line_count': 5})
        self.assertEqual((by_id['end_line'], by_id['next_start_line']), (5, 6))
        self.assertIn('not the XML id of a view', self.refused('get_view', {'model': 'res.partner',
                                                                             'xmlid': 'base.main_company'}))
        actions = self.structured('list_actions', {'model': 'kaitech.mcp.key'})
        self.assertIn('kaitech_mcp_server.action_mcp_key', [row['xmlid'] for row in actions['window_actions']])
        company = self.structured('lookup_xmlid', {'xmlid': 'base.main_company'})['xmlids'][0]
        self.assertEqual((company['model'], company['res_id']), ('res.company', self.company_id))
        self.assertNotIn('display_name', company)
        partner = self.structured('lookup_xmlid', {'xmlid': 'base.main_partner'})['xmlids'][0]
        self.assertIn('display_name', partner)
        reverse = self.structured('lookup_xmlid', {'model': 'res.company', 'res_id': self.company_id})
        self.assertIn('base.main_company', [row['xmlid'] for row in reverse['xmlids']])
        searched = self.structured('lookup_xmlid', {'query': 'base.main_comp'})
        self.assertIn('base.main_company', [row['xmlid'] for row in searched['xmlids']])

    def test_explore_calls_are_audited(self):
        self.structured('describe_model', {'model': 'res.partner'})
        call = self.last_call(self.key_id)
        self.assertEqual((call['result'], call['tool'], call['model']), ('ok', 'describe_model', 'res.partner'))
