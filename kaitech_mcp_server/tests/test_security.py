import json
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import common, tagged

from ..models import mcp_generated_module
from .common import new_token, new_user


@tagged('post_install', '-at_install')
class TestTokenOwnership(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = new_user(self.env, 'mcp_owner_user')
        self.admin = new_user(self.env, 'mcp_owner_admin', groups=('base.group_user', 'base.group_system'))
        self.token, _raw = new_token(self.env, self.user)
        self.as_user = self.token.with_user(self.user)

    def test_an_owner_cannot_re_own_re_key_revive_or_widen_a_token(self):
        for values in ({'user_id': self.admin.id}, {'token': 'f' * 64}, {'expires_at': '2099-01-01'},
                       {'allow_sql': True}, {'allow_explore': True}, {'scope': 'admin'},
                       {'sql_table_ids': [(0, 0, {'name': 'res_partner'})]}):
            with self.assertRaises(AccessError, msg=values):
                self.as_user.write(values)
        self.as_user.write({'state': 'revoked', 'name': 'renamed'})
        with self.assertRaises(AccessError):
            self.as_user.write({'state': 'active'})
        self.assertEqual((self.token.user_id, self.token.state), (self.user, 'revoked'))

    def test_an_owner_cannot_create_a_token_directly(self):
        with self.assertRaises(AccessError):
            self.env['mcp.token'].with_user(self.user).create({'user_id': self.user.id, 'token': 'f' * 64})

    def test_token_issuance_is_not_callable_remotely(self):
        Token = self.env['mcp.token'].with_user(self.user)
        for method in ('issue', 'issue_oauth', 'refresh'):
            with self.assertRaises(AccessError, msg=method):
                get_public_method(Token, method)
        for method in ('create_code', 'exchange'):
            with self.assertRaises(AccessError, msg=method):
                get_public_method(self.env['mcp.auth.code'].with_user(self.user), method)

    def test_a_refreshed_token_keeps_its_scope_and_restrictions(self):
        partner_model = self.env['ir.model']._get('res.partner')
        raw_access, raw_refresh = self.env['mcp.token'].sudo().issue_oauth(self.user.id, 'oauth client')
        token = self.env['mcp.token'].sudo().search([('user_id', '=', self.user.id), ('token_type', '=', 'oauth')])
        token.write({'scope': 'read', 'allowed_model_ids': [(6, 0, partner_model.ids)], 'ip_allowlist': '10.0.0.1',
                     'field_restrictions': json.dumps({'res.partner': ['name']}), 'allow_explore': True})
        self.assertTrue(raw_access)
        self.env['mcp.token'].sudo().refresh(raw_refresh)
        rotated = self.env['mcp.token'].sudo().search(
            [('user_id', '=', self.user.id), ('token_type', '=', 'oauth'), ('state', '=', 'active')])
        self.assertEqual(len(rotated), 1)
        self.assertEqual((rotated.scope, rotated.allowed_model_ids, rotated.ip_allowlist, rotated.allow_explore),
                         ('read', partner_model, '10.0.0.1', True))
        self.assertEqual(json.loads(rotated.field_restrictions), {'res.partner': ['name']})


@tagged('post_install', '-at_install')
class TestOwnedRecords(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = new_user(self.env, 'mcp_page_user')
        self.other = new_user(self.env, 'mcp_page_other')
        self.admin = new_user(self.env, 'mcp_page_admin', groups=('base.group_user', 'base.group_system'))

    def test_a_portal_page_cannot_be_handed_to_another_owner(self):
        page = self.env['mcp.portal.page'].with_user(self.user).create({'name': 'Mine', 'spec': '{}'})
        with self.assertRaises(AccessError):
            page.write({'created_by': self.admin.id})
        with self.assertRaises(AccessError):
            self.env['mcp.portal.page'].with_user(self.user).create(
                {'name': 'Forged', 'spec': '{}', 'created_by': self.admin.id})
        page.with_user(self.admin).write({'created_by': self.other.id})
        self.assertEqual(page.created_by, self.other)

    def test_an_artifact_is_private_to_its_owner(self):
        artifact = self.env['mcp.html.artifact'].with_user(self.user).create({'name': 'Chart', 'html': '<p>x</p>'})
        self.assertFalse(self.env['mcp.html.artifact'].with_user(self.other).search([('id', '=', artifact.id)]))
        with self.assertRaises(AccessError):
            artifact.with_user(self.other).write({'is_public': True})
        with self.assertRaises(AccessError):
            artifact.with_user(self.user).write({'created_by': self.other.id})

    def test_jobs_run_as_their_owner_and_cannot_be_forged(self):
        Job = self.env['mcp.job']
        with self.assertRaises(AccessError):
            Job.with_user(self.user).create({'operation': 'custom', 'user_id': self.admin.id})
        partner = self.env['res.partner'].create({'name': 'Job target', 'company_name': 'Job Company'})
        payload = {'args': {'model': 'res.partner', 'method': 'create_company', 'ids': [partner.id]},
                   'mcp_scope': 'write'}
        job = Job.sudo().create({'operation': 'custom', 'user_id': self.user.id, 'mcp_scope': 'write',
                                 'payload': json.dumps(payload)})
        with self.assertRaises(AccessError):
            job.with_user(self.user).write({'user_id': self.admin.id})
        seen = []
        original = type(self.env['res.partner']).create_company

        def spy(records):
            seen.append((records.env.uid, records.env.su))
            return original(records)

        with patch.object(type(self.env['res.partner']), 'create_company', spy):
            job.action_run()
        self.assertEqual(seen, [(self.user.id, False)])
        self.assertEqual(job.state, 'done', job.error_message)

    def test_installing_a_generated_module_needs_the_server_switch(self):
        module = self.env['mcp.generated.module'].create(
            {'name': 'Sale', 'technical_name': 'sale', 'state': 'generated', 'spec': '{}'})
        module.attachment_id = self.env['ir.attachment'].create({'name': 'sale.zip', 'raw': b'PK'})
        with tempfile.TemporaryDirectory() as folder:
            self.env['ir.config_parameter'].sudo().set_param('mcp_server.generated_modules_path', folder)
            with patch.object(mcp_generated_module, 'config', {}):
                with self.assertRaisesRegex(UserError, 'switched off on this server'):
                    module.action_install()
            with patch.object(mcp_generated_module, 'config', {'mcp_module_generator': 'True'}):
                with self.assertRaisesRegex(UserError, 'already exists'):
                    module.action_install()
