# -*- coding: utf-8 -*-
from contextlib import contextmanager

from odoo.addons.base.res import res_partner

from .common import EndpointCase, new_key, new_user


def rename_tag(self, name):
    self.write({'name': name})
    return self.mapped('name')


def commit_inside(self, name):
    self.write({'name': name})
    self.env.cr.commit()
    return True


def rollback_inside(self, name):
    self.write({'name': name})
    self.env.cr.rollback()
    return True


class TestCallMethod(EndpointCase):

    def setUp(self):
        super(TestCallMethod, self).setUp()
        with self.shared_env() as env:
            self.writer = new_user(env, 'mcp_methods_writer')
            self.reader = new_user(env, 'mcp_methods_reader', groups=('base.group_user',))
            key, self.token = new_key(env, self.writer, {'res.partner.category': {'read', 'call'},
                                                         'res.partner': {'read', 'call'},
                                                         'res.partner.title': {'read'}}, allow_methods=True)
            self.key_id = key.id
            _off, self.off_token = new_key(env, self.writer, {'res.partner.category': {'read', 'call'}},
                                           name='methods off')
            _reader, self.reader_token = new_key(env, self.reader, {'res.partner.category': {'read', 'call'}},
                                                 allow_methods=True, name='reader')
            self.tag_id = env['res.partner.category'].create({'name': 'Mcp Tag'}).id
            self.partner_id = env['res.partner'].create({'name': 'Mcp Contact'}).id

    @contextmanager
    def probe(self, name, function):
        """Give res.partner.category a method for the duration of one test, then remove it."""
        setattr(res_partner.PartnerCategory, name, function)
        self.registry.__dict__.pop('_kaitech_mcp_index', None)
        try:
            yield
        finally:
            delattr(res_partner.PartnerCategory, name)
            self.registry.__dict__.pop('_kaitech_mcp_index', None)

    def run_method(self, token, arguments):
        return self.call(token, 'call_method', arguments)

    def rename(self, token, method='mcp_rename_probe', **extra):
        arguments = {'model': 'res.partner.category', 'method': method, 'ids': [self.tag_id], 'args': ['Renamed']}
        arguments.update(extra)
        return self.run_method(token, arguments)

    def tag_name(self):
        with self.shared_env() as env:
            return env['res.partner.category'].browse(self.tag_id).name

    def test_dry_run_reports_changes_and_rolls_back(self):
        with self.probe('mcp_rename_probe', rename_tag):
            result = self.rename(self.token, dry_run=True)
        self.assertFalse(result['isError'], result)
        content = result['structuredContent']
        self.assertTrue(content['dry_run'])
        self.assertEqual(content['result'], ['Renamed'])
        self.assertEqual(content['changes'][str(self.tag_id)]['name'], ['Mcp Tag', 'Renamed'])
        self.assertEqual(self.tag_name(), 'Mcp Tag')
        with self.shared_env() as env:
            env.cr.execute("SELECT tool, method_name, dry_run, is_write, result, fields_written FROM kaitech_mcp_call "
                           "WHERE key_id = %s ORDER BY id DESC LIMIT 1", (self.key_id,))
            row = env.cr.fetchone()
        self.assertEqual(row[:5], ('call_method', 'mcp_rename_probe', True, True, 'ok'))
        self.assertIn('res.partner.category.name', row[5].split(','))
        self.assertNotIn('Renamed', row[5])

    def test_real_call_writes(self):
        with self.probe('mcp_rename_probe', rename_tag):
            result = self.rename(self.token)
        self.assertFalse(result['isError'], result)
        self.assertFalse(result['structuredContent']['dry_run'])
        self.assertEqual(self.tag_name(), 'Renamed')

    def test_dry_run_refuses_a_method_that_commits(self):
        with self.probe('mcp_commit_probe', commit_inside):
            result = self.rename(self.token, method='mcp_commit_probe', dry_run=True)
        self.assertTrue(result['isError'])
        self.assertIn('commits or rolls back the transaction itself', result['structuredContent']['error'])
        self.assertEqual(self.tag_name(), 'Mcp Tag')

    def test_dry_run_refuses_a_method_that_rolls_back_the_transaction(self):
        with self.probe('mcp_rollback_probe', rollback_inside):
            result = self.rename(self.token, method='mcp_rollback_probe', dry_run=True)
            self.assertTrue(result['isError'])
            self.assertIn('commits or rolls back the transaction itself', result['structuredContent']['error'])
            self.assertEqual(self.tag_name(), 'Mcp Tag')
            with self.probe('mcp_rename_probe', rename_tag):
                again = self.rename(self.token, dry_run=True)
        self.assertFalse(again['isError'], again)
        self.assertEqual(self.tag_name(), 'Mcp Tag')

    def test_refused_methods(self):
        for method, text in (('_compute_commercial_partner', 'Private methods'), ('write', 'ORM method'),
                             ('unlink', 'ORM method'), ('sudo', 'ORM method'), ('no_such_method', 'has no method')):
            result = self.run_method(self.token, {'model': 'res.partner', 'method': method, 'ids': [self.partner_id]})
            self.assertTrue(result['isError'], method)
            self.assertIn(text, result['structuredContent']['error'], method)
        result = self.run_method(self.token, {'model': 'res.partner.title', 'method': 'name_get'})
        self.assertIn('may not call methods of res.partner.title', result['structuredContent']['error'])
        result = self.run_method(self.token, {'model': 'res.partner', 'method': 'create_company', 'ids': [99999999]})
        self.assertIn('Records not found', result['structuredContent']['error'])
        self.assertEqual(self.tag_name(), 'Mcp Tag')

    def test_switch_off_hides_the_tool(self):
        listed = [tool['name'] for tool in self.legacy('tools/list', token=self.off_token)[1]['result']['tools']]
        self.assertNotIn('call_method', listed)
        with self.probe('mcp_rename_probe', rename_tag):
            result = self.rename(self.off_token)
        self.assertEqual(result['structuredContent']['error'], 'There is no tool named call_method.')
        self.assertEqual(self.tag_name(), 'Mcp Tag')
        self.assertIn('call_method', [tool['name'] for tool in self.legacy('tools/list', token=self.token)[1]['result']['tools']])

    def test_odoo_access_rights_still_apply(self):
        with self.probe('mcp_rename_probe', rename_tag):
            result = self.rename(self.reader_token)
        self.assertTrue(result['isError'])
        self.assertIn('not allowed', result['structuredContent']['error'])
        self.assertEqual(self.tag_name(), 'Mcp Tag')
