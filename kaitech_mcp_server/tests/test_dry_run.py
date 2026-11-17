from .common import McpCase, marker, new_token, new_user


class TestDryRun(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.admin = new_user(env, 'mcp_dry_admin', groups=('base.group_user', 'base.group_system'))
            self.writer = new_user(env, 'mcp_dry_writer')
            _token, self.admin_raw = new_token(env, self.admin, scope='admin')
            _token, self.writer_raw = new_token(env, self.writer, scope='write')
            self.name = marker()
            self.partner = env['res.partner'].create({'name': self.name})
            self.category = env['res.partner.category'].create({'name': self.name})
            self.messages = self.partner_messages(env)

    def partner_messages(self, env):
        return env['mail.message'].search_count([('model', '=', 'res.partner'), ('res_id', '=', self.partner.id)])

    def rows(self, answer, table):
        return next(row for row in answer['changes']['rows'] if row['table'] == table)

    def test_write_dry_run_reports_the_difference_and_keeps_nothing(self):
        result = self.call(self.writer_raw, 'odoo_write', {
            'model': 'res.partner', 'ids': [self.partner.id], 'values': {'name': 'renamed', 'city': 'Bandung'},
            'dry_run': True,
        })
        self.assertFalse(result['isError'], result)
        answer = result['structuredContent']
        self.assertTrue(answer['dry_run'])
        self.assertEqual(answer['updated'], 1)
        [difference] = answer['changes']['differences']
        self.assertEqual(difference['id'], self.partner.id)
        self.assertEqual(difference['fields']['name'], {'before': self.name, 'after': 'renamed'})
        self.assertEqual(difference['fields']['city'], {'before': False, 'after': 'Bandung'})
        self.assertGreaterEqual(self.rows(answer, 'res_partner')['updated'], 1)
        with self.shared_env() as env:
            partner = env['res.partner'].browse(self.partner.id)
            self.assertEqual((partner.name, partner.city), (self.name, False))
            self.assertEqual(self.partner_messages(env), self.messages, 'the rolled back rename left a tracking message')
        self.assertTrue(self.last_call(self.writer)['dry_run'])

    def test_write_without_dry_run_still_writes(self):
        result = self.call(self.writer_raw, 'odoo_write', {
            'model': 'res.partner', 'ids': [self.partner.id], 'values': {'city': 'Bandung'},
        })
        self.assertEqual(result['structuredContent'], {'updated': 1, 'model': 'res.partner', 'ids': [self.partner.id]})
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].browse(self.partner.id).city, 'Bandung')
        self.assertFalse(self.last_call(self.writer)['dry_run'])

    def test_write_dry_run_shows_only_the_fields_the_token_may_read(self):
        values = {'model': 'res.partner', 'ids': [self.partner.id], 'values': {'name': 'renamed'}, 'dry_run': True}
        [difference] = self.call(self.writer_raw, 'odoo_write', values)['structuredContent']['changes']['differences']
        self.assertIn('complete_name', difference['fields'])
        with self.shared_env() as env:
            token = env['mcp.token'].search([('user_id', '=', self.writer.id)])
            token.field_restrictions = '{"res.partner": ["name"]}'
        [difference] = self.call(self.writer_raw, 'odoo_write', values)['structuredContent']['changes']['differences']
        self.assertEqual(set(difference['fields']), {'name'})

    def test_rows_of_models_outside_the_token_allowlist_are_counted_not_named(self):
        values = {'model': 'res.partner', 'values': {'name': marker(), 'category_id': [(6, 0, [self.category.id])]},
                  'dry_run': True}
        changes = self.call(self.writer_raw, 'odoo_create', values)['structuredContent']['changes']
        self.assertIn('res_partner_res_partner_category_rel', [row['table'] for row in changes['rows']])
        self.assertNotIn('rows_not_shown', changes)
        with self.shared_env() as env:
            token = env['mcp.token'].search([('user_id', '=', self.writer.id)])
            token.allowed_model_ids = env['ir.model']._get('res.partner')
        changes = self.call(self.writer_raw, 'odoo_create', values)['structuredContent']['changes']
        self.assertEqual({row['model'] for row in changes['rows']}, {'res.partner'})
        self.assertGreaterEqual(changes['rows_not_shown'], 1)

    def test_create_dry_run_names_the_record_and_keeps_nothing(self):
        name = marker()
        result = self.call(self.writer_raw, 'odoo_create', {
            'model': 'res.partner', 'values': {'name': name, 'city': 'Bandung'}, 'dry_run': True,
        })
        answer = result['structuredContent']
        self.assertEqual(answer['changes']['created']['values']['name'], name)
        self.assertEqual(answer['changes']['created']['id'], answer['id'])
        self.assertEqual(self.rows(answer, 'res_partner')['inserted'], 1)
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].search_count([('name', '=', name)]))

    def test_unlink_dry_run_lists_the_records_and_keeps_them(self):
        result = self.call(self.admin_raw, 'odoo_unlink', {
            'model': 'res.partner', 'ids': [self.partner.id], 'dry_run': True,
        })
        answer = result['structuredContent']
        self.assertEqual(answer['changes']['deleted'], [{'id': self.partner.id, 'display_name': self.name}])
        self.assertEqual(self.rows(answer, 'res_partner')['deleted'], 1)
        with self.shared_env() as env:
            self.assertTrue(env['res.partner'].browse(self.partner.id).exists())

    def test_call_method_dry_run_reports_the_difference(self):
        result = self.call(self.admin_raw, 'odoo_call_method', {
            'model': 'res.partner', 'method': 'action_archive', 'ids': [self.partner.id], 'dry_run': True,
        })
        answer = result['structuredContent']
        self.assertTrue(answer['dry_run'])
        [difference] = answer['changes']['differences']
        self.assertEqual(difference['fields']['active'], {'before': True, 'after': False})
        with self.shared_env() as env:
            self.assertTrue(env['res.partner'].browse(self.partner.id).active)
        self.assertTrue(self.last_call(self.admin)['dry_run'])

    def test_dry_run_must_be_a_boolean(self):
        result = self.call(self.writer_raw, 'odoo_write', {
            'model': 'res.partner', 'ids': [self.partner.id], 'values': {'city': 'Bandung'}, 'dry_run': 'yes',
        })
        self.assertIn("'dry_run' must be true or false", result['structuredContent']['error'])
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].browse(self.partner.id).city)

    def test_tools_list_advertises_the_new_parameters(self):
        status, body = self.rpc('tools/list', raw=self.admin_raw)
        tools = {tool['name']: tool for tool in body['result']['tools']}
        for name in ('odoo_create', 'odoo_write', 'odoo_unlink', 'odoo_call_method'):
            self.assertIn('cannot be rolled back', tools[name]['inputSchema']['properties']['dry_run']['description'])
        self.assertIn('compact', tools['odoo_search_read']['inputSchema']['properties'])
        self.assertIn('odoo_batch', tools)
        self.assertIn('server_log_tail', tools)
