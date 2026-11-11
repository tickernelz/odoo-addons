import os
import tempfile

from ..services import explore
from .common import McpCase, new_token, new_user

EXPLORE_TOOLS = ['describe_model', 'search_methods', 'describe_method', 'get_method_source', 'search_code',
                 'read_source', 'list_modules', 'describe_module', 'get_view', 'list_actions', 'search_xmlids']


class TestExplore(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_explore_user')
            _key, self.token = new_token(env, self.user, allow_explore=True)
            _key, self.closed_token = new_token(env, self.user)

    def explore(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertFalse(result['isError'], result['structuredContent'])
        return result['structuredContent']

    def refused(self, tool, arguments, token=None):
        result = self.call(token or self.token, tool, arguments)
        self.assertTrue(result['isError'], result['structuredContent'])
        return result['structuredContent']['error']

    def test_explore_tools_exist_only_for_keys_that_allow_them(self):
        names = self.tool_names(self.token)
        self.assertEqual([name for name in names if name in EXPLORE_TOOLS], EXPLORE_TOOLS)
        self.assertFalse(set(self.tool_names(self.closed_token)) & set(EXPLORE_TOOLS))
        for tool in EXPLORE_TOOLS:
            self.assertIn('may not explore code', self.refused(tool, {}, token=self.closed_token))

    def test_describe_model_explains_fields_methods_and_access_in_one_call(self):
        described = self.explore('describe_model', {'model': 'res.partner'})
        self.assertEqual(described['defined_in'], 'base')
        line = described['fields']['base']['commercial_partner_id']
        self.assertTrue(line.startswith('many2one(res.partner)'), line)
        self.assertIn('compute=_compute_commercial_partner(', line)
        with self.shared_env() as env:
            chains = explore.index(env).chains(type(env['res.partner']))
        listed = set(described['methods'])
        counted = set()
        for group in described['inherited_public_methods'].values():
            self.assertEqual(group['count'], len(group['methods']))
            counted |= set(group['methods'])
        self.assertEqual(listed - set(chains), set(), 'describe_model invented a method')
        self.assertEqual(counted - set(chains), set(), 'describe_model invented an inherited method')
        self.assertTrue(listed, 'describe_model lists no method of res.partner')
        from_parents = [name for name in chains if not name.startswith('_')
                        and 'res.partner' not in [item.owner for item in chains[name]]
                        and [item.owner for item in chains[name]][-1] not in ('base', None)]
        self.assertEqual(len(counted), len(from_parents), 'a method inherited from a mixin is not named')
        self.assertNotIn('write', listed)
        self.assertIn('write', described['orm_overrides'])
        self.assertEqual(described['access']['token'],
                         {'read': True, 'create': True, 'update': True, 'delete': False, 'call': False})
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
        everywhere = self.explore('search_code', {'query': 'mcp.pat.wizard', 'file_types': ['xml']})
        self.assertTrue(all(match.split('/')[0] for match in everywhere['matches']))
        self.assertTrue(any(match.startswith('kaitech_mcp_server/') for match in everywhere['matches']))
        manifest = self.explore('read_source', {'path': 'kaitech_mcp_server/__manifest__.py'})
        self.assertIn("'depends': ['base', 'web', 'mail']", manifest['text'])
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
        modules = self.explore('list_modules', {'query': 'kaitech_mcp_server'})
        self.assertEqual([row['name'] for row in modules['modules']], ['kaitech_mcp_server'])
        module = self.explore('describe_module', {'module': 'kaitech_mcp_server'})
        self.assertIn('mcp.token', module['models_defined'])
        self.assertIn('mcp.pat.wizard', module['wizards'])
        self.assertIn('kaitech_mcp_server.mcp_menu_tokens', module['records']['ir.ui.menu']['xmlids'])
        self.assertEqual(module['depends'], ['base', 'web', 'mail'])
        view = self.explore('get_view', {'model': 'res.partner', 'view_type': 'form', 'lines': 5})
        self.assertTrue(view['arch'].startswith('<form'))
        self.assertTrue(any(row.startswith('base.view_partner_form ') for row in view['views']), view['views'])
        self.assertEqual(view['next'], 'get_view(model, view_type, start_line=6)')
        actions = self.explore('list_actions', {'model': 'res.partner'})
        self.assertIn('base.action_partner_form', [row['action'] for row in actions['actions']])
        xmlids = self.explore('search_xmlids', {'query': 'base.view_partner_form'})
        self.assertTrue(any(row.startswith('base.view_partner_form -> ir.ui.view,') for row in xmlids['xmlids']))

    def test_the_index_follows_the_registry_and_is_audited(self):
        with self.shared_env() as env:
            first = explore.index(env)
            self.assertIs(explore.index(env), first)
        self.explore('describe_model', {'model': 'res.partner'})
        self.assertEqual(self.last_call(self.user)['tool'], 'describe_model')
        self.assertFalse(self.last_call(self.user)['is_error'])
