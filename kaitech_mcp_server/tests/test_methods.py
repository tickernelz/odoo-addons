# -*- coding: utf-8 -*-
from odoo.addons.base.res import res_partner

from .common import EndpointCase, new_key, new_user


def commit_inside(self):
    self.write({'comment': 'committed by a method'})
    self.env.cr.commit()
    return True


def rollback_inside(self):
    self.write({'comment': 'rolled back by a method'})
    self.env.cr.rollback()
    return True


class TestCallMethod(EndpointCase):

    def setUp(self):
        super(TestCallMethod, self).setUp()
        with self.shared_env() as env:
            self.writer = new_user(env, 'mcp_methods_writer')
            self.reader = new_user(env, 'mcp_methods_reader', groups=('base.group_user',))
            key, self.token = new_key(env, self.writer, {'res.partner': {'read', 'call'},
                                                         'res.partner.category': {'read'}}, allow_methods=True)
            self.key_id = key.id
            _off, self.off_token = new_key(env, self.writer, {'res.partner': {'read', 'call'}}, name='methods off')
            _reader, self.reader_token = new_key(env, self.reader, {'res.partner': {'read', 'call'}},
                                                 allow_methods=True, name='reader')
            self.partner_id = env['res.partner'].create({'name': 'Mcp Contact', 'company_name': 'Mcp Dry Co'}).id

    def run_method(self, token, arguments):
        return self.call(token, 'call_method', arguments)

    def company_state(self):
        with self.shared_env() as env:
            partner = env['res.partner'].browse(self.partner_id)
            return partner.parent_id.name, env['res.partner'].search_count([('name', '=', 'Mcp Dry Co')]), partner.comment

    def test_dry_run_reports_changes_and_rolls_back(self):
        result = self.run_method(self.token, {'model': 'res.partner', 'method': 'create_company',
                                              'ids': [self.partner_id], 'dry_run': True})
        self.assertFalse(result['isError'], result)
        content = result['structuredContent']
        self.assertTrue(content['dry_run'])
        before, after = content['changes'][str(self.partner_id)]['parent_id']
        self.assertFalse(before)
        self.assertEqual(after[1], 'Mcp Dry Co')
        self.assertEqual(self.company_state(), (False, 0, False))
        with self.shared_env() as env:
            env.cr.execute("SELECT tool, method_name, dry_run, is_write, result, fields_written FROM kaitech_mcp_call "
                           "WHERE key_id = %s ORDER BY id DESC LIMIT 1", (self.key_id,))
            self.assertEqual(env.cr.fetchone()[:5], ('call_method', 'create_company', True, True, 'ok'))

    def test_real_call_writes(self):
        result = self.run_method(self.token, {'model': 'res.partner', 'method': 'create_company',
                                              'ids': [self.partner_id]})
        self.assertFalse(result['isError'], result)
        self.assertFalse(result['structuredContent']['dry_run'])
        self.assertEqual(self.company_state()[:2], ('Mcp Dry Co', 1))

    def test_dry_run_refuses_a_method_that_commits(self):
        res_partner.Partner.mcp_commit_probe = commit_inside
        self.registry.__dict__.pop('_kaitech_mcp_index', None)
        try:
            result = self.run_method(self.token, {'model': 'res.partner', 'method': 'mcp_commit_probe',
                                                  'ids': [self.partner_id], 'dry_run': True})
        finally:
            del res_partner.Partner.mcp_commit_probe
            self.registry.__dict__.pop('_kaitech_mcp_index', None)
        self.assertTrue(result['isError'])
        self.assertIn('commits or rolls back the transaction itself', result['structuredContent']['error'])
        self.assertEqual(self.company_state()[2], False)

    def test_dry_run_refuses_a_method_that_rolls_back_the_transaction(self):
        res_partner.Partner.mcp_rollback_probe = rollback_inside
        self.registry.__dict__.pop('_kaitech_mcp_index', None)
        try:
            result = self.run_method(self.token, {'model': 'res.partner', 'method': 'mcp_rollback_probe',
                                                  'ids': [self.partner_id], 'dry_run': True})
        finally:
            del res_partner.Partner.mcp_rollback_probe
            self.registry.__dict__.pop('_kaitech_mcp_index', None)
        self.assertTrue(result['isError'])
        self.assertIn('rolls back the transaction itself', result['structuredContent']['error'])
        self.assertEqual(self.company_state()[2], False)
        again = self.run_method(self.token, {'model': 'res.partner', 'method': 'create_company',
                                             'ids': [self.partner_id], 'dry_run': True})
        self.assertFalse(again['isError'], again)

    def test_refused_methods(self):
        for method, text in (('_compute_commercial_partner', 'Private methods'), ('write', 'ORM method'),
                             ('unlink', 'ORM method'), ('sudo', 'ORM method'), ('no_such_method', 'has no method')):
            result = self.run_method(self.token, {'model': 'res.partner', 'method': method, 'ids': [self.partner_id]})
            self.assertTrue(result['isError'], method)
            self.assertIn(text, result['structuredContent']['error'], method)
        result = self.run_method(self.token, {'model': 'res.partner.category', 'method': 'name_get'})
        self.assertIn('may not call methods of res.partner.category', result['structuredContent']['error'])
        result = self.run_method(self.token, {'model': 'res.partner', 'method': 'create_company', 'ids': [99999999]})
        self.assertIn('Records not found', result['structuredContent']['error'])
        self.assertEqual(self.company_state(), (False, 0, False))

    def test_switch_off_hides_the_tool(self):
        listed = [tool['name'] for tool in self.legacy('tools/list', token=self.off_token)[1]['result']['tools']]
        self.assertNotIn('call_method', listed)
        result = self.run_method(self.off_token, {'model': 'res.partner', 'method': 'create_company',
                                                  'ids': [self.partner_id]})
        self.assertEqual(result['structuredContent']['error'], 'There is no tool named call_method.')
        self.assertIn('call_method', [tool['name'] for tool in self.legacy('tools/list', token=self.token)[1]['result']['tools']])

    def test_odoo_access_rights_still_apply(self):
        result = self.run_method(self.reader_token, {'model': 'res.partner', 'method': 'create_company',
                                                     'ids': [self.partner_id]})
        self.assertTrue(result['isError'])
        self.assertEqual(self.company_state(), (False, 0, False))
