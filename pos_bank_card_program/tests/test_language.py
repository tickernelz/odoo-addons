import json

from odoo.http import root
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosLanguage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        lang = cls.env['res.lang'].with_context(active_test=False).search([
            ('code', '=', 'id_ID'),
        ])
        if lang and not lang.active:
            lang.write({'active': True})
        cls.cashier = new_test_user(
            cls.env, login='langcashier',
            groups='base.group_user,point_of_sale.group_pos_user')
        cls.outsider = new_test_user(
            cls.env, login='langoutsider',
            groups='base.group_user,point_of_sale.group_pos_user')
        config = cls.env['pos.config'].create({'name': 'Outlet Language'})
        cls.pos_session = cls.env['pos.session'].create({
            'user_id': cls.cashier.id,
            'config_id': config.id,
        })

    def call_route(self, route, params=None):
        response = self.url_open(
            route,
            data=json.dumps({
                'id': 0,
                'jsonrpc': '2.0',
                'method': 'call',
                'params': params or {},
            }),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def error_message(self, payload):
        return payload['error']['data']['message']

    def test_list_returns_installed_languages_and_current(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.call_route('/pos_bank_card_program/lang/list')
        result = payload['result']
        self.assertEqual(result['current'], 'en_US')
        codes = [lang['code'] for lang in result['langs']]
        self.assertIn('en_US', codes)
        self.assertIn('id_ID', codes)
        self.assertTrue(all(lang['name'] for lang in result['langs']))

    def test_set_writes_user_language_and_session_context(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.call_route(
            '/pos_bank_card_program/lang/set', {'code': 'id_ID'})
        self.assertEqual(payload['result'], 'id_ID')
        self.assertEqual(self.cashier.lang, 'id_ID')
        sid = self.opener.cookies.get('session_id')
        session = root.session_store.get(sid)
        self.assertEqual(session.context.get('lang'), 'id_ID')
        payload = self.call_route(
            '/pos_bank_card_program/lang/set', {'code': 'en_US'})
        self.assertEqual(payload['result'], 'en_US')
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_set_rejects_a_language_that_is_not_installed(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.call_route(
            '/pos_bank_card_program/lang/set', {'code': 'su_ID'})
        self.assertIn('error', payload)
        self.assertIn('su_ID', self.error_message(payload))
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_routes_reject_a_user_without_an_open_pos_session(self):
        self.authenticate('langoutsider', 'langoutsider')
        for route, params in (
            ('/pos_bank_card_program/lang/list', None),
            ('/pos_bank_card_program/lang/set', {'code': 'id_ID'}),
        ):
            payload = self.call_route(route, params)
            self.assertIn('error', payload, route)
            self.assertIn('point of sale session', self.error_message(payload))
        self.assertEqual(self.outsider.lang, 'en_US')

    def test_set_cannot_change_another_users_language(self):
        self.authenticate('langcashier', 'langcashier')
        self.call_route('/pos_bank_card_program/lang/set', {'code': 'id_ID'})
        self.assertEqual(self.cashier.lang, 'id_ID')
        self.assertEqual(self.outsider.lang, 'en_US')
        self.call_route('/pos_bank_card_program/lang/set', {'code': 'en_US'})
