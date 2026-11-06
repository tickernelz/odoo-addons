import os
import tempfile

from odoo.tools import mute_logger

from .. import explore
from .common import EndpointCase, new_key, new_user
from .test_language import INDONESIAN, load_indonesian

EXPLORE_TOOLS = ['describe_model', 'search_methods', 'describe_method', 'get_method_source', 'search_code',
                 'read_source', 'list_modules', 'describe_module', 'get_view', 'list_actions', 'search_xmlids']


class TestExplore(EndpointCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_explore_user')
            key, self.token = new_key(env, self.user, {'res.partner': {'read'}}, allow_explore=True)
            self.key_id = key.id
            _key, self.closed_token = new_key(env, self.user, {'res.partner': {'read'}}, name='closed client')

    def explore(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertFalse(result['isError'], result['structuredContent'])
        return result['structuredContent']

    def refused(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertTrue(result['isError'], result['structuredContent'])
        return result['structuredContent']['error']

    def test_explore_tools_exist_only_for_keys_that_allow_them(self):
        names = [tool['name'] for tool in self.legacy('tools/list', token=self.token)[1]['result']['tools']]
        self.assertEqual([name for name in names if name in EXPLORE_TOOLS], EXPLORE_TOOLS)
        self.assertNotIn('call_method', names)
        closed = [tool['name'] for tool in self.legacy('tools/list', token=self.closed_token)[1]['result']['tools']]
        self.assertFalse(set(closed) & set(EXPLORE_TOOLS))
        for tool in EXPLORE_TOOLS:
            self.assertEqual(self.refused(tool, {}, token=self.closed_token), 'There is no tool named %s.' % tool)
        self.assertIn('Explore code', self.refused('list_models', {'all': True}, token=self.closed_token))

    def test_describe_model_explains_fields_methods_and_access_in_one_call(self):
        described = self.explore('describe_model', {'model': 'res.partner'})
        self.assertEqual(described['defined_in'], 'base')
        line = described['fields']['base']['commercial_partner_id']
        self.assertTrue(line.startswith('many2one(res.partner)'), line)
        self.assertIn('compute=_compute_commercial_partner(', line)
        self.assertIn('address_get', described['methods'])
        self.assertNotIn('write', described['methods'])
        self.assertIn('write', described['orm_overrides'])
        self.assertEqual(described['access']['key'], {'read': True, 'create': False, 'update': False, 'delete': False})
        self.assertTrue(described['access']['user']['read'])
        some = self.explore('describe_model', {'model': 'res.partner', 'query': 'commercial'})
        self.assertEqual(sorted(some['fields']['base']), ['commercial_company_name', 'commercial_partner_id'])
        self.assertIn('Unknown model', self.refused('describe_model', {'model': 'no.such.model'}))

    def test_methods_are_found_with_their_override_chain_and_source(self):
        found = self.explore('search_methods', {'query': 'commercial partner', 'model': 'res.partner'})
        self.assertIn('_compute_commercial_partner', [row['method'] for row in found['methods']])
        everywhere = self.explore('search_methods', {'query': 'address_get'})
        self.assertEqual(everywhere['methods'][0]['model'], 'res.partner')
        method = self.explore('describe_method', {'model': 'res.partner', 'method': '_compute_commercial_partner'})
        self.assertIn('commercial_partner_id (compute)', method['used_by_fields'])
        self.assertTrue(method['decorators']['depends'])
        self.assertEqual(method['call_method'], 'refused: private method')
        self.assertTrue(method['overrides'][0]['path'].startswith('base/models/res_partner.py:'))
        self.assertEqual(self.explore('describe_method', {'model': 'res.partner', 'method': 'write'})['call_method'],
                         'refused: ORM method, use the record tools')
        source = self.explore('get_method_source', {'model': 'res.partner', 'method': 'address_get'})
        first = source['definitions'][0]
        self.assertEqual(first['module'], 'base')
        self.assertIn('def address_get(self', first['source'])
        start, end = [int(part) for part in first['lines'].split('-')]
        listed = self.explore('read_source', {'path': 'base/models/res_partner.py', 'start_line': start, 'lines': 1})
        self.assertIn('address_get', listed['text'])
        self.assertLess(start, end)

    def test_code_search_and_reading_stay_inside_module_folders(self):
        found = self.explore('search_code', {'query': 'def address_get', 'module': 'base'})
        self.assertTrue(any(match.startswith('base/models/res_partner.py:') for match in found['matches']), found)
        everywhere = self.explore('search_code', {'query': 'kaitech.mcp.key.wizard', 'file_types': ['xml']})
        self.assertTrue(all(match.split('/')[0] for match in everywhere['matches']))
        self.assertTrue(any(match.startswith('kaitech_mcp/') for match in everywhere['matches']))
        manifest = self.explore('read_source', {'path': 'kaitech_mcp/__manifest__.py'})
        self.assertIn("'depends': ['base']", manifest['text'])
        for path in ('../etc/passwd', '/etc/passwd', 'base/../../odoo/orm/models.py', 'base/static/description/icon.png',
                     'no_such_module/__init__.py', 'base/__init__.py\x00.py'):
            self.refused('read_source', {'path': path})

    def test_a_module_on_disk_but_not_installed_is_refused(self):
        with self.shared_env() as env:
            state = env['ir.module.module'].search([('name', '=', 'sale')], limit=1).state
            if state != 'uninstalled':
                self.skipTest('sale is installed in this database')
        self.assertIn('Unknown module: sale', self.refused('read_source', {'path': 'sale/models/sale_order.py'}))
        self.assertIn('Unknown module: sale', self.refused('search_code', {'query': 'class SaleOrder', 'module': 'sale'}))
        self.assertIn('base/__init__.py', self.explore('read_source', {'path': 'base/__init__.py'})['path'])

    def test_a_symlink_out_of_a_module_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            module = os.path.join(folder, 'mcp_probe_module')
            os.mkdir(module)
            with open(os.path.join(module, 'inside.py'), 'w') as handle:
                handle.write('INSIDE = 1\n')
            outside = os.path.join(folder, 'outside.py')
            with open(outside, 'w') as handle:
                handle.write('SECRET = 1\n')
            os.symlink(outside, os.path.join(module, 'link.py'))
            with self.shared_env() as env:
                idx = explore.index(env)
                idx._installed = frozenset(explore.installed_modules(env)) | {'mcp_probe_module'}
                idx._roots['mcp_probe_module'] = os.path.realpath(module)
            self.assertIn('INSIDE', self.explore('read_source', {'path': 'mcp_probe_module/inside.py'})['text'])
            self.assertIn('No such file', self.refused('read_source', {'path': 'mcp_probe_module/link.py'}))
            found = self.explore('search_code', {'query': 'SECRET', 'module': 'mcp_probe_module'})
            self.assertEqual(found['matches'], [])

    def test_modules_views_actions_and_xmlids(self):
        modules = self.explore('list_modules', {'query': 'kaitech_mcp'})
        self.assertEqual([row['name'] for row in modules['modules']], ['kaitech_mcp'])
        module = self.explore('describe_module', {'module': 'kaitech_mcp'})
        self.assertIn('kaitech.mcp.key', module['models_defined'])
        self.assertIn('kaitech.mcp.key.wizard', module['wizards'])
        self.assertIn('kaitech_mcp.menu_mcp_keys', module['records']['ir.ui.menu']['xmlids'])
        self.assertEqual(module['depends'], ['base'])
        view = self.explore('get_view', {'model': 'res.partner', 'view_type': 'form', 'lines': 5})
        self.assertTrue(view['arch'].startswith('<form'))
        self.assertTrue(any(row.startswith('base.view_partner_form ') for row in view['views']), view['views'])
        self.assertEqual(view['next'], 'get_view(model, view_type, start_line=6)')
        actions = self.explore('list_actions', {'model': 'res.partner'})
        self.assertIn('base.action_partner_form', [row['action'] for row in actions['actions']])
        xmlids = self.explore('search_xmlids', {'query': 'base.view_partner_form'})
        self.assertTrue(any(row.startswith('base.view_partner_form -> ir.ui.view,') for row in xmlids['xmlids']))
        models = self.explore('list_models', {'all': True, 'query': 'res.partner.category'})
        self.assertEqual(models['models'][0][:3], ['res.partner.category', 'Partner Tags', 'base'])
        with self.shared_env() as env:
            having = sorted(name for name in env.registry if 'country_id' in env.registry[name]._fields)
        found = self.explore('list_models', {'all': True, 'field': 'country_id', 'limit': 100})
        self.assertEqual([row[0] for row in found['models']], having[:100])
        self.assertEqual(found['total'], len(having))
        self.assertTrue(all(row[4] == 'many2one(res.country)' for row in found['models'] if row[0] == 'res.partner'))
        self.assertIn('only with all', self.refused('list_models', {'field': 'country_id'}))

    def test_the_index_follows_the_registry_and_is_audited(self):
        with self.shared_env() as env:
            first = explore.index(env)
            self.assertIs(explore.index(env), first)
        self.explore('describe_model', {'model': 'res.partner'})
        self.assertEqual(self.last_call(self.key_id)['tool'], 'describe_model')
        self.assertEqual(self.last_call(self.key_id)['result'], 'ok')

    def test_overview_resource(self):
        listed = self.legacy('resources/list', token=self.token)[1]['result']['resources']
        self.assertEqual([item['uri'] for item in listed], ['odoo://overview'])
        read = self.legacy('resources/read', {'uri': 'odoo://overview'}, token=self.token)[1]['result']
        self.assertIn('"explore_code": true', read['contents'][0]['text'])
        with mute_logger('odoo.addons.kaitech_mcp.endpoint'):
            missing = self.legacy('resources/read', {'uri': 'odoo://secret'}, token=self.token)[1]
        self.assertEqual(missing['error']['code'], -32002)
        self.assertEqual(self.legacy('resources/list', token=self.closed_token)[1]['result']['resources'], [])
        with mute_logger('odoo.addons.kaitech_mcp.endpoint'):
            closed = self.legacy('resources/read', {'uri': 'odoo://overview'}, token=self.closed_token)[1]
        self.assertEqual(closed['error']['code'], -32002)
        self.assertIn('describe_model', self.legacy('initialize', {'protocolVersion': '2025-11-25'},
                                                    token=self.token)[1]['result']['instructions'])

    def test_field_labels_follow_the_key_user_language(self):
        with self.shared_env() as env:
            load_indonesian(env, modules=('base',))
            indonesian = new_user(env, 'mcp_explore_id', lang=INDONESIAN)
            _key, token = new_key(env, indonesian, {'res.partner': {'read'}}, allow_explore=True)
            english_label = env['res.partner']._fields['country_id']._description_string(env(context={'lang': 'en_US'}))
            translated = env['res.partner']._fields['country_id']._description_string(env(context={'lang': INDONESIAN}))
        self.assertNotEqual(english_label, translated)
        line = self.explore('describe_model', {'model': 'res.partner', 'fields': ['country_id']}, token=token)
        self.assertIn(repr(translated), line['fields']['base']['country_id'])
