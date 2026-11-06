# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import common

from .. import grants, keys
from .common import WRITER, new_key, new_user, set_setting


@common.at_install(False)
@common.post_install(True)
class TestKeys(common.TransactionCase):

    def setUp(self):
        super(TestKeys, self).setUp()
        self.user = new_user(self.env, 'mcp_key_user')
        self.manager = new_user(self.env, 'mcp_key_manager',
                                groups=('base.group_user', WRITER, 'kaitech_mcp_server.group_mcp_manager'))

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

    def test_constant_time_fallback_compares_like_hmac(self):
        self.assertTrue(keys._compare(b'abc', b'abc'))
        self.assertFalse(keys._compare(b'abc', b'abd'))
        self.assertFalse(keys._compare(b'abc', b'ab'))

    def test_administrator_and_portal_users_are_refused(self):
        with self.assertRaisesRegexp(ValidationError, 'Administrator'):
            new_key(self.env, self.env['res.users'].browse(SUPERUSER_ID), {})
        portal = new_user(self.env, 'mcp_portal', groups=('base.group_portal',))
        with self.assertRaises(ValidationError):
            new_key(self.env, portal, {})

    def test_forbidden_model_cannot_be_granted(self):
        for model in ('res.users', 'ir.config_parameter', 'kaitech.mcp.key'):
            with self.assertRaises(ValidationError):
                new_key(self.env, self.user, {model: {'read'}})

    def test_delete_needs_the_setting_and_switching_it_off_clears_it(self):
        with self.assertRaisesRegexp(ValidationError, 'switched off'):
            new_key(self.env, self.user, {'res.partner': {'read', 'delete'}})
        set_setting(self.env, 'delete_enabled', 'True')
        key, _token = new_key(self.env, self.user, {'res.partner': {'read', 'delete'}})
        self.assertTrue(key.can_delete)
        settings = self.env['kaitech.mcp.settings'].create({})
        settings.write(dict(settings.default_get(list(settings._fields)), delete_enabled=False))
        settings.execute()
        key.invalidate_cache()
        self.assertFalse(key.can_delete)

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
        wizard = self.env['kaitech.mcp.key.wizard'].sudo(self.manager.id).create({
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

    def test_settings_refuse_everyone_but_administrators(self):
        clerk = new_user(self.env, 'mcp_settings_clerk', groups=('base.group_user',))
        portal = new_user(self.env, 'mcp_settings_portal', groups=('base.group_portal',))
        Settings = self.env['kaitech.mcp.settings']
        changes = dict(Settings.default_get(list(Settings._fields)), sql_enabled=True, audit_retention_days=1,
                       default_rate_per_minute=100000, extra_forbidden_fields=False)
        for user in (clerk, portal):
            with self.assertRaises(AccessError):
                Settings.sudo(user.id).new(changes).set_mcp_values()
            with self.assertRaises(AccessError):
                Settings.sudo(user.id).get_default_mcp_values([])
        self.assertFalse(grants.read_settings(self.env.cr)['sql_enabled'])
        self.assertEqual(grants.read_settings(self.env.cr)['audit_retention_days'], 90)

    def test_settings_store_each_type_faithfully(self):
        settings = self.env['kaitech.mcp.settings'].create({})
        settings.write({'extra_forbidden_models': False, 'sql_statement_timeout_ms': 2500, 'delete_enabled': False})
        settings.execute()
        stored = grants.read_settings(self.env.cr)
        self.assertEqual((stored['extra_forbidden_models'], stored['sql_statement_timeout_ms'], stored['delete_enabled']),
                         ('', 2500, False))

    def test_a_manager_cannot_hand_out_more_rights_than_they_hold(self):
        administrator = new_user(self.env, 'mcp_key_sysadmin', groups=('base.group_user', 'base.group_system'))
        keys_as_manager = self.env['kaitech.mcp.key'].sudo(self.manager.id)
        with self.assertRaisesRegexp(ValidationError, 'groups you do not have'):
            keys_as_manager.create({'name': 'escalate', 'user_id': administrator.id})
        wizard = self.env['kaitech.mcp.key.wizard'].sudo(self.manager.id).create({
            'name': 'escalate', 'user_id': administrator.id})
        with self.assertRaisesRegexp(ValidationError, 'groups you do not have'):
            wizard.action_next()
        self.assertTrue(keys_as_manager.create({'name': 'fine', 'user_id': self.user.id}))

    def test_only_managers_manage_keys(self):
        clerk = new_user(self.env, 'mcp_clerk', groups=('base.group_user',))
        with self.assertRaises(AccessError):
            self.env['kaitech.mcp.key'].sudo(clerk.id).create({'name': 'x', 'user_id': self.user.id})
        with self.assertRaises(AccessError):
            self.env['kaitech.mcp.overview'].sudo(clerk.id).action_open()
        for model in ('kaitech.mcp.key.wizard', 'kaitech.mcp.key.test'):
            with self.assertRaises(AccessError):
                self.env[model].sudo(clerk.id).create({'name': 'x', 'user_id': self.user.id} if 'wizard' in model
                                                      else {'key_id': new_key(self.env, self.user, {})[0].id})

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
