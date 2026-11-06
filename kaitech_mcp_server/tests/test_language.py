# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID
from odoo.exceptions import ValidationError

from .common import EndpointCase, new_key, new_user

INDONESIAN = 'id_ID'


def load_indonesian(env):
    env['res.lang'].load_lang(INDONESIAN)
    env['ir.translation'].load_module_terms(['kaitech_mcp_server'], [INDONESIAN])


class TestLanguage(EndpointCase):

    def setUp(self):
        super(TestLanguage, self).setUp()
        with self.shared_env() as env:
            load_indonesian(env)
            self.english = new_user(env, 'mcp_lang_en')
            self.indonesian = new_user(env, 'mcp_lang_id', lang=INDONESIAN)
            _key, self.english_token = new_key(env, self.english, {'res.partner': {'read'}})
            _key, self.indonesian_token = new_key(env, self.indonesian, {'res.partner': {'read'}})

    def test_screens_follow_the_user_language(self):
        with self.shared_env() as env:
            indonesian = env['kaitech.mcp.key'].with_context(lang=INDONESIAN)
            english = env['kaitech.mcp.key'].with_context(lang='en_US')
            self.assertEqual(english.fields_get(['user_id'])['user_id']['string'], 'Acts as user')
            self.assertEqual(indonesian.fields_get(['user_id'])['user_id']['string'], 'Bertindak sebagai user')
            states = dict(indonesian.fields_get(['state'])['state']['selection'])
            self.assertEqual(states['revoked'], 'Dicabut')
            menu = env.ref('kaitech_mcp_server.menu_mcp_keys').with_context(lang=INDONESIAN)
            self.assertEqual(menu.name, 'Kunci')
            results = dict(env['kaitech.mcp.call'].with_context(lang=INDONESIAN).fields_get(['result'])['result']['selection'])
            self.assertEqual(results['limited'], 'Dibatasi')
            arch = env['kaitech.mcp.key'].with_context(lang=INDONESIAN).fields_view_get(view_type='form')['arch']
            self.assertIn('Putar kunci', arch)
            self.assertNotIn('Rotate key', arch)

    def test_python_messages_follow_the_user_language(self):
        with self.shared_env() as env:
            admin = env['res.users'].browse(SUPERUSER_ID)
            with self.assertRaisesRegexp(ValidationError, 'Administrator'):
                env['kaitech.mcp.key'].with_context(lang='en_US').create({'name': 'x', 'user_id': admin.id})
            with self.assertRaisesRegexp(ValidationError, 'Kunci MCP tidak boleh'):
                env['kaitech.mcp.key'].with_context(lang=INDONESIAN).create({'name': 'x', 'user_id': admin.id})

    def test_tool_definitions_are_identical_for_every_language(self):
        english = self.legacy('tools/list', token=self.english_token)[1]['result']
        indonesian = self.legacy('tools/list', token=self.indonesian_token)[1]['result']
        self.assertEqual(english, indonesian)

    def test_record_data_follows_the_key_user_language(self):
        with self.shared_env() as env:
            env.cr.execute("SELECT count(*) FROM ir_translation WHERE lang = %s AND name = 'res.country,name'", (INDONESIAN,))
            if not env.cr.fetchone()[0]:
                env['ir.translation'].load_module_terms(['base'], [INDONESIAN])
        domain = [['code', '=', 'DE']]
        english = self.call(self.english_token, 'search_records', {'model': 'res.country', 'domain': domain})
        self.assertTrue(english['isError'])
        with self.shared_env() as env:
            for login in ('mcp_lang_en', 'mcp_lang_id'):
                user = env['res.users'].search([('login', '=', login)])
                key = env['kaitech.mcp.key'].search([('user_id', '=', user.id)])
                key.write({'permission_ids': [(0, 0, {
                    'model_id': env['ir.model'].search([('model', '=', 'res.country')]).id, 'can_read': True})]})
        english = self.call(self.english_token, 'search_records', {'model': 'res.country', 'domain': domain, 'fields': ['name']})
        indonesian = self.call(self.indonesian_token, 'search_records', {'model': 'res.country', 'domain': domain,
                                                                         'fields': ['name']})
        self.assertEqual(english['structuredContent']['records'][0]['name'], 'Germany')
        self.assertEqual(indonesian['structuredContent']['records'][0]['name'], 'Jerman')
