from unittest.mock import patch

from .common import McpCase, new_token, new_user


class TestCallMethod(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.admin = new_user(env, 'mcp_call_admin', groups=('base.group_user', 'base.group_system'))
            self.user = new_user(env, 'mcp_call_user')
            _token, self.admin_raw = new_token(env, self.admin, scope='admin')
            _token, self.user_raw = new_token(env, self.user, scope='admin')
            self.partner = env['res.partner'].create({'name': 'MCP call target', 'company_name': 'MCP Call Company'})

    def call_method(self, raw, method, model='res.partner', **arguments):
        return self.call(raw, 'odoo_call_method', dict(arguments, model=model, method=method))

    def test_mail_gateway_methods_are_refused_even_with_full_access(self):
        with self.shared_env() as env:
            partner_class = type(env['res.partner'])
        methods = [name for name in ('message_process', 'message_route', 'message_route_verify', 'message_route_process')
                   if callable(getattr(partner_class, name, None))]
        self.assertIn('message_process', methods)
        for method in methods:
            for raw in (self.admin_raw, self.user_raw):
                result = self.call_method(raw, method, args=['res.partner', 'From: a@example.com\n\nhi'])
                self.assertTrue(result['isError'], (method, result))
                self.assertIn('mail gateway method', result['structuredContent']['error'], method)

    def test_api_private_methods_are_refused_even_with_full_access(self):
        result = self.call_method(self.admin_raw, 'issue', model='mcp.token', args=[self.admin.id])
        self.assertIn('marked @api.private', result['structuredContent']['error'])
        with self.shared_env() as env:
            self.assertEqual(env['mcp.token'].search_count([('user_id', '=', self.admin.id)]), 1)

    def test_private_and_orm_methods_need_full_access_of_a_settings_administrator(self):
        for method in ('_compute_display_name', 'write', 'sudo'):
            result = self.call_method(self.user_raw, method, ids=[self.partner.id], args=[{'name': 'x'}])
            self.assertTrue(result['isError'], method)
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].browse(self.partner.id).name, 'MCP call target')

    def test_a_business_method_runs(self):
        result = self.call_method(self.user_raw, 'create_company', ids=[self.partner.id])
        self.assertFalse(result['isError'], result)
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].browse(self.partner.id).parent_id.name, 'MCP Call Company')

    def test_dry_run_rolls_everything_back(self):
        result = self.call_method(self.user_raw, 'create_company', ids=[self.partner.id], dry_run=True)
        self.assertFalse(result['isError'], result)
        self.assertTrue(result['structuredContent']['dry_run'])
        with self.shared_env() as env:
            self.assertFalse(env['res.partner'].browse(self.partner.id).parent_id)

    def test_dry_run_refuses_a_method_that_commits_by_itself(self):
        with self.shared_env() as env:
            partner_class = type(env['res.partner'])

        def committing(records):
            records.write({'name': 'committed'})
            records.env.cr.commit()

        with patch.object(partner_class, 'create_company', committing, create=False):
            result = self.call_method(self.user_raw, 'create_company', ids=[self.partner.id], dry_run=True)
        self.assertIn('commits by itself', result['structuredContent']['error'])
        with self.shared_env() as env:
            self.assertEqual(env['res.partner'].browse(self.partner.id).name, 'MCP call target')

    def test_call_method_needs_admin_scope(self):
        with self.shared_env() as env:
            _token, raw = new_token(env, self.user)
        result = self.call_method(raw, 'create_company', ids=[self.partner.id])
        self.assertIn("requires 'admin' scope", result['structuredContent']['error'])
