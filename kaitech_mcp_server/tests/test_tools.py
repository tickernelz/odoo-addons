# -*- coding: utf-8 -*-
from .common import EndpointCase, new_key, new_user, set_setting


class TestTools(EndpointCase):

    def setUp(self):
        super(TestTools, self).setUp()
        with self.shared_env() as env:
            self.sales = new_user(env, 'mcp_tools_sales')
            self.reader = new_user(env, 'mcp_tools_reader', groups=('base.group_user',))
            key, self.token = new_key(env, self.sales, {
                'res.partner': {'read', 'create', 'update'}, 'res.country': {'read'},
                'res.partner.category': {'read'}})
            self.key_id = key.id
            reader_key, self.reader_token = new_key(env, self.reader, {'res.partner': {'read', 'create', 'update'}})
            self.reader_key_id = reader_key.id
            self.category = env['res.partner.category'].create({'name': 'MCP VIP'})
            self.partner = env['res.partner'].create({'name': 'PT Contoh Jakarta', 'city': 'Jakarta', 'phone': '021-1'})

    def test_list_models_combines_key_and_odoo_rights(self):
        result = self.call(self.reader_token, 'list_models', {})
        models = dict((row['model'], row['can']) for row in result['structuredContent']['models'])
        self.assertEqual(models['res.partner'], {'read': True, 'create': False, 'update': False, 'delete': False})
        result = self.call(self.token, 'list_models', {'query': 'countr'})
        self.assertEqual([row['model'] for row in result['structuredContent']['models']], ['res.country'])

    def test_get_model_fields_hides_forbidden_fields(self):
        result = self.call(self.token, 'get_model_fields', {'model': 'res.partner'})
        fields = result['structuredContent']['fields']
        self.assertEqual(fields['country_id']['relation'], 'res.country')
        self.assertFalse(fields['user_id'].get('relation_readable', True))
        for hidden in ('image', 'signup_token'):
            self.assertNotIn(hidden, fields)

    def test_search_read_count_and_audit(self):
        result = self.call(self.token, 'search_records', {
            'model': 'res.partner', 'domain': [['city', 'ilike', 'jakarta']], 'fields': ['name', 'phone'], 'limit': 5})
        self.assertFalse(result['isError'])
        records = result['structuredContent']['records']
        self.assertEqual([(row['id'], row['name']) for row in records], [(self.partner.id, 'PT Contoh Jakarta')])
        self.assertIn('PT Contoh Jakarta', result['content'][0]['text'])
        call = self.last_call(self.key_id)
        self.assertEqual((call['result'], call['tool'], call['model'], call['record_count']),
                         ('ok', 'search_records', 'res.partner', 1))
        result = self.call(self.token, 'count_records', {'model': 'res.partner', 'domain': [['city', '=', 'Jakarta']]})
        self.assertEqual(result['structuredContent'], {'count': 1})
        result = self.call(self.token, 'read_records', {'model': 'res.partner', 'ids': [self.partner.id, 99999999],
                                                        'fields': ['city']})
        self.assertEqual(result['structuredContent']['missing_ids'], [99999999])
        self.assertEqual(result['structuredContent']['records'][0]['city'], 'Jakarta')

    def test_paging_reports_has_more(self):
        first = self.call(self.token, 'search_records', {'model': 'res.country', 'limit': 2, 'fields': ['code']})
        content = first['structuredContent']
        self.assertTrue(content['has_more'])
        self.assertEqual(content['next_offset'], 2)
        second = self.call(self.token, 'search_records', {'model': 'res.country', 'limit': 2, 'offset': 2,
                                                          'fields': ['code']})
        self.assertNotEqual(content['records'][0]['id'], second['structuredContent']['records'][0]['id'])
        result = self.call(self.token, 'search_records', {'model': 'res.country', 'limit': 100000})
        self.assertTrue(result['isError'])

    def test_unpermitted_and_forbidden_models_look_unknown(self):
        for model in ('res.users', 'ir.config_parameter', 'res.bank', 'no.such.model'):
            result = self.call(self.token, 'search_records', {'model': model})
            self.assertTrue(result['isError'])
            self.assertIn('Unknown model', result['structuredContent']['error'])
        self.assertEqual(self.last_call(self.key_id)['result'], 'invalid_arguments')
        result = self.call(self.token, 'create_record', {'model': 'res.country', 'values': {'name': 'X'}})
        self.assertIn('may not create', result['structuredContent']['error'])
        self.assertEqual(self.last_call(self.key_id)['result'], 'refused')

    def test_create_with_lines_and_update_many2many(self):
        result = self.call(self.token, 'create_record', {'model': 'res.partner', 'values': {
            'name': 'PT Baru', 'is_company': True, 'child_ids': [{'name': 'Budi'}, {'name': 'Sari'}]}})
        self.assertFalse(result['isError'], result)
        new_id = result['structuredContent']['id']
        with self.shared_env() as env:
            partner = env['res.partner'].browse(new_id)
            self.assertEqual(sorted(partner.child_ids.mapped('name')), ['Budi', 'Sari'])
            self.assertEqual(partner.create_uid.id, self.sales.id)
        call = self.last_call(self.key_id)
        self.assertEqual(call['fields_written'], 'res.partner.child_ids,res.partner.is_company,res.partner.name')
        result = self.call(self.token, 'update_records', {'model': 'res.partner', 'ids': [new_id, self.partner.id],
                                                          'values': {'category_id': [self.category.id]}})
        self.assertEqual(result['structuredContent'], {'updated': 2})
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].browse(new_id).category_id.ids, [self.category.id])

    def test_odoo_access_rights_still_apply_and_nothing_is_written(self):
        result = self.call(self.reader_token, 'create_record', {'model': 'res.partner', 'values': {'name': 'Nope'}})
        self.assertTrue(result['isError'])
        self.assertEqual(self.last_call(self.reader_key_id)['result'], 'refused')
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].search([('name', '=', 'Nope')]))

    def test_failed_write_rolls_back_but_stays_in_the_audit(self):
        result = self.call(self.token, 'create_record', {'model': 'res.partner', 'values': {
            'name': 'Broken', 'child_ids': [{'name': 'Line'}], 'parent_id': 99999999}})
        self.assertTrue(result['isError'])
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].search([('name', 'in', ['Broken', 'Line'])]))
        self.assertEqual(self.last_call(self.key_id)['result'], 'error')

    def test_raw_context_and_unknown_arguments_are_refused(self):
        result = self.call(self.token, 'search_records', {'model': 'res.partner', 'context': {'active_test': False}})
        self.assertTrue(result['isError'])
        self.assertIn('Unexpected arguments', result['structuredContent']['error'])
        result = self.call(self.token, 'no_such_tool', {})
        self.assertTrue(result['isError'])

    def test_archived_records_only_on_request(self):
        with self.shared_env() as env:
            env['res.partner'].create({'name': 'Archived MCP', 'active': False})
        domain = [['name', '=', 'Archived MCP']]
        hidden = self.call(self.token, 'count_records', {'model': 'res.partner', 'domain': domain})
        shown = self.call(self.token, 'count_records', {'model': 'res.partner', 'domain': domain, 'include_archived': True})
        self.assertEqual((hidden['structuredContent']['count'], shown['structuredContent']['count']), (0, 1))

    def test_delete_is_off_by_default_then_bounded_and_audited(self):
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertNotIn('delete_records', [tool['name'] for tool in body['result']['tools']])
        with self.shared_env() as env:
            set_setting(env, 'delete_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).permission_ids.filtered(
                lambda row: row.model_name == 'res.partner').write({'can_delete': True})
            victims = env['res.partner'].create({'name': 'Delete me'}) | env['res.partner'].create({'name': 'Delete me too'})
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertIn('delete_records', [tool['name'] for tool in body['result']['tools']])
        too_many = self.call(self.token, 'delete_records', {'model': 'res.partner', 'ids': range(1, 22)})
        self.assertTrue(too_many['isError'])
        result = self.call(self.token, 'delete_records', {'model': 'res.partner', 'ids': victims.ids})
        self.assertEqual(sorted(row['display_name'] for row in result['structuredContent']['deleted']),
                         ['Delete me', 'Delete me too'])
        call = self.last_call(self.key_id)
        self.assertIn('%s Delete me' % victims[0].id, call['deleted_names'])
        result = self.call(self.token, 'delete_records', {'model': 'res.country', 'ids': [1]})
        self.assertTrue(result['isError'])
