from unittest.mock import patch

from .common import EndpointCase, marker, new_key, new_user


class TestCallMethod(EndpointCase):

    def setUp(self):
        super().setUp()
        self.mark = marker()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_call_user')
            key, self.token = new_key(env, self.user, {'res.partner': {'read', 'call'}, 'res.country': {'read', 'call'}},
                                      allow_call=True)
            self.key_id = key.id
            _key, self.switched_off = new_key(env, self.user, {'res.partner': {'read', 'call'}}, name='off client')
            _key, self.no_row = new_key(env, self.user, {'res.partner': {'read'}, 'res.country': {'read', 'call'}},
                                        allow_call=True, name='row client')
            self.partner = env['res.partner'].create({'name': 'Budi %s' % self.mark, 'company_name': 'PT %s' % self.mark})

    def call_method(self, arguments, token=None):
        return self.call(token or self.token, 'call_method', dict({'model': 'res.partner'}, **arguments))

    def partner_state(self):
        with self.shared_env() as env:
            partner = env['res.partner'].browse(self.partner.id)
            return partner.parent_id.id, env['res.partner'].search_count([('name', '=', 'PT %s' % self.mark)])

    def test_a_business_method_runs_and_reports_what_changed(self):
        result = self.call_method({'method': 'create_company', 'ids': [self.partner.id]})
        self.assertFalse(result['isError'], result['structuredContent'])
        changes = result['structuredContent']['changes'][str(self.partner.id)]
        self.assertFalse(changes['parent_id'][0])
        self.assertEqual(changes['parent_id'][1][1], 'PT %s' % self.mark)
        parent, companies = self.partner_state()
        self.assertTrue(parent)
        self.assertEqual(companies, 1)
        audit = self.last_call(self.key_id)
        self.assertEqual((audit['tool'], audit['result'], audit['model']), ('call_method', 'ok', 'res.partner'))
        self.assertIn('res.partner.parent_id', audit['fields_written'])

    def test_dry_run_reports_the_changes_and_rolls_everything_back(self):
        result = self.call_method({'method': 'create_company', 'ids': [self.partner.id], 'dry_run': True})
        self.assertFalse(result['isError'], result['structuredContent'])
        self.assertTrue(result['structuredContent']['dry_run'])
        self.assertIn('parent_id', result['structuredContent']['changes'][str(self.partner.id)])
        self.assertEqual(self.partner_state(), (False, 0))
        with self.shared_env() as env:
            env.cr.execute("SELECT dry_run FROM kaitech_mcp_call WHERE key_id = %s ORDER BY id DESC LIMIT 1", (self.key_id,))
            self.assertTrue(env.cr.fetchone()[0])

    def test_dry_run_refuses_a_method_that_commits_by_itself(self):
        def committing(records):
            records.write({'comment': 'committed'})
            records.env.cr.commit()
        with self.shared_env() as env:
            klass = type(env['res.partner'])
        with patch.object(klass, 'address_get', committing, create=False):
            result = self.call_method({'method': 'address_get', 'ids': [self.partner.id], 'dry_run': True})
        self.assertTrue(result['isError'])
        self.assertIn('commits by itself', result['structuredContent']['error'])
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].browse(self.partner.id).comment)

    def test_model_methods_take_no_ids_and_results_are_plain_json(self):
        email = '%s@example.com' % self.mark
        with_ids = self.call_method({'method': 'find_or_create', 'ids': [self.partner.id], 'args': [email]})
        self.assertIn('without ids', with_ids['structuredContent']['error'])
        made = self.call_method({'method': 'find_or_create', 'args': [email], 'dry_run': True})
        self.assertFalse(made['isError'], made['structuredContent'])
        self.assertEqual(made['structuredContent']['result']['model'], 'res.partner')
        self.assertEqual(len(made['structuredContent']['result']['ids']), 1)
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].search_count([('email', '=', email)]), 0)
        found = self.call_method({'method': 'address_get', 'ids': [self.partner.id], 'args': [['contact']]})
        self.assertEqual(found['structuredContent']['result'], {'contact': self.partner.id})
        self.assertEqual(found['structuredContent']['changes'], {})

    def test_records_the_key_may_not_read_come_back_without_names(self):
        def currencies(records):
            return records.env['res.currency'].sudo().search([], limit=2)

        def partners(records):
            return records.env['res.partner'].sudo().search([('id', '=', records.ids[0])])

        with self.shared_env() as env:
            klass = type(env['res.partner'])
        with patch.object(klass, 'address_get', currencies, create=False):
            result = self.call_method({'method': 'address_get', 'ids': [self.partner.id]})
        self.assertFalse(result['isError'], result['structuredContent'])
        shown = result['structuredContent']['result']
        self.assertEqual(shown['model'], 'res.currency')
        self.assertTrue(shown['ids'])
        self.assertNotIn('names', shown, shown)
        with patch.object(klass, 'address_get', partners, create=False):
            own = self.call_method({'method': 'address_get', 'ids': [self.partner.id]})
        names = own['structuredContent']['result']['names']
        self.assertEqual(len(names), 1)
        self.assertIn(self.mark, names[0])

    def test_private_orm_and_unknown_methods_are_refused(self):
        for method, reason in (('_compute_commercial_partner', 'private method'), ('write', 'ORM method'),
                               ('unlink', 'ORM method'), ('sudo', '@api.private'), ('nothing_here', 'not a method')):
            result = self.call_method({'method': method, 'ids': [self.partner.id]})
            self.assertTrue(result['isError'], method)
            self.assertIn(reason, result['structuredContent']['error'])
        self.assertEqual(self.last_call(self.key_id)['result'], 'refused')
        self.assertEqual(self.partner_state(), (False, 0))

    def test_keys_without_the_switch_or_the_model_row_are_refused(self):
        hidden = self.call_method({'method': 'create_company', 'ids': [self.partner.id]}, token=self.switched_off)
        self.assertEqual(hidden['structuredContent']['error'], 'There is no tool named call_method.')
        names = [tool['name'] for tool in self.legacy('tools/list', token=self.switched_off)[1]['result']['tools']]
        self.assertNotIn('call_method', names)
        row_refused = self.call_method({'method': 'create_company', 'ids': [self.partner.id]}, token=self.no_row)
        self.assertIn('may not call methods of res.partner', row_refused['structuredContent']['error'])
        self.assertEqual(self.partner_state(), (False, 0))

    def test_odoo_access_rights_still_apply(self):
        with self.shared_env() as env:
            reader = new_user(env, 'mcp_call_reader', groups=('base.group_user',))
            _key, token = new_key(env, reader, {'res.partner': {'read', 'call'}}, allow_call=True)
        result = self.call_method({'method': 'create_company', 'ids': [self.partner.id]}, token=token)
        self.assertTrue(result['isError'])
        self.assertEqual(self.partner_state(), (False, 0))
