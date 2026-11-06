from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import common

from .. import keys
from .common import new_key, new_user, set_setting


@common.tagged('post_install', '-at_install')
class TestKeys(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = new_user(self.env, 'mcp_key_user')
        self.manager = new_user(self.env, 'mcp_key_manager',
                                groups=('base.group_user', 'base.group_partner_manager', 'kaitech_mcp.group_mcp_manager'))

    def test_token_is_shown_once_and_stored_as_a_digest(self):
        key, token = new_key(self.env, self.user, {'res.partner': {'read'}})
        self.assertTrue(token.startswith(keys.PREFIX))
        self.assertEqual(key.key_index, token[len(keys.PREFIX):][:keys.INDEX_LENGTH])
        self.env.cr.execute("SELECT key_hash FROM kaitech_mcp_key WHERE id = %s", (key.id,))
        stored = self.env.cr.fetchone()[0]
        self.assertNotIn(token, stored)
        self.assertTrue(keys.matches(token, stored))
        self.assertFalse(keys.matches(token + 'x', stored))
        with self.assertRaises(UserError):
            key.write({'key_hash': 'x'})

    def test_two_tokens_never_repeat(self):
        self.assertNotEqual(keys.new_token(), keys.new_token())
        self.assertEqual(len(keys.new_token()), len(keys.PREFIX) + 43)

    def test_administrator_and_portal_users_are_refused(self):
        with self.assertRaisesRegex(ValidationError, 'Administrator'):
            new_key(self.env, self.env['res.users'].browse(SUPERUSER_ID), {})
        portal = new_user(self.env, 'mcp_portal', groups=('base.group_portal',))
        with self.assertRaises(ValidationError):
            new_key(self.env, portal, {})

    def test_forbidden_model_cannot_be_granted(self):
        for model in ('res.users', 'ir.config_parameter', 'kaitech.mcp.key'):
            with self.assertRaises(ValidationError):
                new_key(self.env, self.user, {model: {'read'}})

    def test_delete_needs_the_setting_and_switching_it_off_clears_it(self):
        with self.assertRaisesRegex(ValidationError, 'switched off'):
            new_key(self.env, self.user, {'res.partner': {'read', 'delete'}})
        set_setting(self.env, 'delete_enabled', 'True')
        key, _token = new_key(self.env, self.user, {'res.partner': {'read', 'delete'}})
        self.assertTrue(key.can_delete)
        settings = self.env['kaitech.mcp.settings'].create({})
        settings.write(dict(settings.default_get(list(settings._fields)), delete_enabled=False))
        settings.action_save()
        key.invalidate_recordset()
        self.assertFalse(key.can_delete)

    def test_state_search_combines_several_states(self):
        active, _token = new_key(self.env, self.user, {'res.partner': {'read'}})
        expiring, _token = new_key(self.env, self.user, {'res.partner': {'read'}},
                                   expires_at=datetime.utcnow() + timedelta(days=3))
        revoked, _token = new_key(self.env, self.user, {'res.partner': {'read'}})
        revoked.action_revoke()
        keys = self.env['kaitech.mcp.key']
        mine = active | expiring | revoked
        self.assertEqual(keys.search([('id', 'in', mine.ids), ('state', 'in', ['active', 'expiring'])]), active | expiring)
        self.assertEqual(keys.search([('id', 'in', mine.ids), ('state', 'not in', ['active'])]), expiring | revoked)
        self.assertEqual(keys.search_count([('id', 'in', mine.ids), ('state', 'in', ['active', 'expiring', 'revoked'])]), 3)

    def test_state_follows_revocation_and_expiry(self):
        key, _token = new_key(self.env, self.user, {'res.partner': {'read'}})
        self.assertEqual(key.state, 'active')
        key.expires_at = fields.Datetime.to_string(datetime.utcnow() + timedelta(days=3))
        self.assertEqual(key.state, 'expiring')
        key.expires_at = fields.Datetime.to_string(datetime.utcnow() - timedelta(minutes=1))
        self.assertEqual(key.state, 'expired')
        self.assertIn(key, self.env['kaitech.mcp.key'].search([('state', '=', 'expired')]))
        key.action_revoke()
        self.assertEqual(key.state, 'revoked')
        self.assertIn(key, self.env['kaitech.mcp.key'].search([('state', 'in', ['revoked'])]))
        self.assertNotIn(key, self.env['kaitech.mcp.key'].search([('state', '!=', 'revoked')]))

    def test_rotation_keeps_permissions_and_revokes_the_old_key(self):
        key, token = new_key(self.env, self.user, {'res.partner': {'read', 'create'}})
        action = key.action_rotate()
        wizard = self.env['kaitech.mcp.key.wizard'].browse(action['res_id'])
        replacement = wizard.key_id
        self.assertEqual(key.state, 'revoked')
        self.assertEqual(replacement.rotated_from_id, key)
        self.assertEqual(replacement.permission_ids.mapped('model_name'), ['res.partner'])
        self.assertTrue(replacement.permission_ids.can_create)
        self.assertTrue(wizard.token and wizard.token != token)
        self.assertIn('Bearer %s' % wizard.token, wizard.config_http)
        wizard.action_done()
        self.assertFalse(wizard.token)

    def test_wizard_creates_a_key_from_ticked_models(self):
        partner_model = self.env['ir.model'].search([('model', '=', 'res.partner')])
        wizard = self.env['kaitech.mcp.key.wizard'].with_user(self.manager.id).create({
            'name': 'Wizard client', 'user_id': self.user.id})
        wizard.action_next()
        self.assertEqual(wizard.step, 'models')
        wizard.write({'line_ids': [(0, 0, {'model_id': partner_model.id, 'can_read': True, 'can_update': True})]})
        self.assertFalse(wizard.line_ids.access_warning)
        wizard.action_create()
        self.assertEqual(wizard.step, 'done')
        self.assertTrue(keys.matches(wizard.token, wizard.key_id.sudo().key_hash))
        self.assertTrue(wizard.key_id.permission_ids.can_update)

    def test_wizard_warns_when_odoo_rights_are_missing(self):
        reader = new_user(self.env, 'mcp_reader_only', groups=('base.group_user',))
        partner_model = self.env['ir.model'].search([('model', '=', 'res.partner')])
        wizard = self.env['kaitech.mcp.key.wizard'].create({'name': 'Reader', 'user_id': reader.id, 'step': 'models'})
        wizard.write({'line_ids': [(0, 0, {'model_id': partner_model.id, 'can_read': True, 'can_create': True})]})
        self.assertIn('Create', wizard.line_ids.access_warning)

    def test_managers_hand_out_keys_only_within_their_own_groups(self):
        stronger = new_user(self.env, 'mcp_key_stronger', groups=('base.group_user', 'base.group_erp_manager'))
        as_manager = self.env['kaitech.mcp.key'].with_user(self.manager.id)
        key = as_manager.create({'name': 'Within', 'user_id': self.user.id})
        with self.assertRaisesRegex(ValidationError, 'access groups you hold'):
            as_manager.create({'name': 'Stronger', 'user_id': stronger.id})
        with self.assertRaisesRegex(ValidationError, 'access groups you hold'):
            key.write({'user_id': stronger.id})
        wizard = self.env['kaitech.mcp.key.wizard'].with_user(self.manager.id).create({
            'name': 'Stronger', 'user_id': stronger.id})
        with self.assertRaisesRegex(ValidationError, 'access groups you hold'):
            wizard.action_next()
        settings_admin = new_user(self.env, 'mcp_key_settings_admin',
                                  groups=('base.group_system', 'kaitech_mcp.group_mcp_manager'))
        self.env['kaitech.mcp.key'].with_user(settings_admin.id).create({'name': 'Any', 'user_id': stronger.id})

    def test_only_managers_manage_keys(self):
        clerk = new_user(self.env, 'mcp_clerk', groups=('base.group_user',))
        with self.assertRaises(AccessError):
            self.env['kaitech.mcp.key'].with_user(clerk.id).create({'name': 'x', 'user_id': self.user.id})
        with self.assertRaises(AccessError):
            self.env['kaitech.mcp.overview'].with_user(clerk.id).action_open()

    def test_retention_removes_only_old_calls(self):
        key, _token = new_key(self.env, self.user, {'res.partner': {'read'}})
        cr = self.env.cr
        for age in (1, 200):
            cr.execute("INSERT INTO kaitech_mcp_call (key_id, method, result, create_date) "
                       "VALUES (%s, 'tools/list', 'ok', (now() AT TIME ZONE 'UTC') - %s * interval '1 day')", (key.id, age))
        self.assertEqual(self.env['kaitech.mcp.call']._purge_old_calls(), 1)
        cr.execute("SELECT count(*) FROM kaitech_mcp_call WHERE key_id = %s", (key.id,))
        self.assertEqual(cr.fetchone()[0], 1)
        set_setting(self.env, 'audit_retention_days', '0')
        self.assertEqual(self.env['kaitech.mcp.call']._purge_old_calls(), 0)
