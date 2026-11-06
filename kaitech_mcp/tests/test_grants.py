from odoo.tests import common

from .. import grants
from .common import grant_for, new_key, new_user, set_setting


@common.tagged('post_install', '-at_install')
class TestGrants(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = new_user(self.env, 'mcp_grant_user')
        self.key, _token = new_key(self.env, self.user, {
            'res.partner': {'read', 'create', 'update'}, 'res.country': {'read'}})
        self.grant = grant_for(self.env, self.key)
        self.partners = self.env['res.partner'].with_user(self.user.id)

    def test_forbidden_models_stay_forbidden_even_with_a_row(self):
        self.grant.rows['res.users'] = {'read': True, 'create': True, 'update': True, 'delete': True, 'fields': None}
        self.grant.rows['ir.config_parameter'] = dict(self.grant.rows['res.users'])
        self.assertFalse(self.grant.permitted(self.env, 'res.users', 'read'))
        self.assertFalse(self.grant.permitted(self.env, 'ir.config_parameter', 'read'))
        with self.assertRaisesRegex(grants.ToolArgumentError, 'Unknown model'):
            self.grant.require(self.env, 'res.users', 'read')

    def test_extra_forbidden_models_from_settings(self):
        set_setting(self.env, 'extra_forbidden_models', 'res.coun*')
        grant = grant_for(self.env, self.key)
        self.assertFalse(grant.permitted(self.env, 'res.country', 'read'))
        self.assertTrue(grant.permitted(self.env, 'res.partner', 'read'))

    def test_delete_needs_the_global_switch(self):
        self.grant.rows['res.partner']['delete'] = True
        self.assertFalse(self.grant.permitted(self.env, 'res.partner', 'delete'))
        self.grant.delete_enabled = True
        self.assertTrue(self.grant.permitted(self.env, 'res.partner', 'delete'))

    def test_secret_and_binary_fields_are_hidden(self):
        fields = self.grant.fields(self.partners)
        self.assertIn('name', fields)
        self.assertNotIn('image_1920', fields)
        self.assertNotIn('signup_token', fields)

    def test_extra_forbidden_field_per_model(self):
        set_setting(self.env, 'extra_forbidden_fields', 'res.partner.vat\nref')
        grant = grant_for(self.env, self.key)
        fields = grant.fields(self.partners)
        self.assertNotIn('vat', fields)
        self.assertNotIn('ref', fields)
        self.assertIn('vat', grant.fields(self.env['res.company'].with_user(self.user.id)))

    def test_listed_fields_restrict_the_model(self):
        name = self.env['ir.model.fields'].search([('model', '=', 'res.partner'), ('name', '=', 'name')])
        self.key.permission_ids.filtered(lambda row: row.model_name == 'res.partner').field_ids = name
        grant = grant_for(self.env, self.key)
        self.assertEqual(sorted(grant.fields(self.partners)), ['id', 'name'])
        with self.assertRaisesRegex(grants.ToolArgumentError, 'forbidden fields'):
            grant.read_fields(self.partners, ['email'])

    def test_domain_operators_and_paths(self):
        countries = self.env['res.country'].with_user(self.user.id)
        good = self.grant.check_domain(self.env, self.partners, ['|', ['name', 'ilike', 'a'], ['country_id.code', '=', 'ID']])
        self.assertEqual(good[1], ('name', 'ilike', 'a'))
        for bad in ([['name', 'any', 'x']], [['name', '=', {'x': 1}]], [['user_id.login', '=', 'admin']],
                    ['name'], [['signup_token', '=', 'x']], 'name = 1'):
            with self.assertRaises(grants.ToolArgumentError):
                self.grant.check_domain(self.env, self.partners, bad)
        self.assertEqual(self.grant.check_domain(self.env, countries, None), [])

    def test_order_accepts_only_stored_allowed_fields(self):
        self.assertEqual(self.grant.check_order(self.partners, 'name desc, id'), 'name desc, id asc')
        for bad in ('name; DROP TABLE res_partner', 'tz_offset', 'signup_token', 'name sideways'):
            with self.assertRaises(grants.ToolArgumentError):
                self.grant.check_order(self.partners, bad)

    def test_values_are_checked_and_converted(self):
        written = set()
        category = self.env['res.partner.category'].create({'name': 'MCP'})
        values = self.grant.check_values(self.env, self.partners, {
            'name': 'PT Contoh', 'category_id': [category.id], 'child_ids': [{'name': 'Budi'}]}, written)
        self.assertEqual(values['category_id'], [(6, 0, [category.id])])
        self.assertEqual(values['child_ids'], [(0, 0, {'name': 'Budi'})])
        self.assertEqual(written, set(['res.partner.name', 'res.partner.category_id', 'res.partner.child_ids']))
        for bad in ({'display_name': 'x'}, {'create_uid': 1}, {'name': 12}, {'category_id': 3},
                    {'commercial_partner_id': 1}, {}, {'image_1920': 'aGVsbG8='}):
            with self.assertRaises(grants.ToolArgumentError):
                self.grant.check_values(self.env, self.partners, bad, set())

    def test_one2many_lines_need_create_on_the_line_model(self):
        self.grant.rows['res.partner']['create'] = False
        with self.assertRaises(grants.ToolRefused):
            self.grant.check_values(self.env, self.partners, {'child_ids': [{'name': 'Budi'}]}, set())

    def test_ids_are_bounded(self):
        self.assertEqual(grants.check_ids([3, 3, 4], 5), [3, 4])
        for bad in ([], [0], [True], ['1'], [1, 2, 3]):
            with self.assertRaises(grants.ToolArgumentError):
                grants.check_ids(bad, 2)
