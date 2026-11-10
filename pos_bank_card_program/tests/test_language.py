import json

from odoo.http import root
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosLanguage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang'].with_context(active_test=False).search([
            ('code', 'in', ['en_US', 'id_ID']),
        ]).write({'active': True})
        cls.cashier = new_test_user(
            cls.env, login='langcashier', lang='en_US',
            groups='base.group_user,point_of_sale.group_pos_user')
        cls.joiner = new_test_user(
            cls.env, login='langjoiner', lang='en_US',
            groups='base.group_user,point_of_sale.group_pos_user')
        cls.outsider = new_test_user(
            cls.env, login='langoutsider', lang='en_US',
            groups='base.group_user,point_of_sale.group_pos_user')
        config = cls.env['pos.config'].create({'name': 'Outlet Language'})
        cls.pos_session = cls.env['pos.session'].create({
            'user_id': cls.cashier.id,
            'config_id': config.id,
        })
        closed_config = cls.env['pos.config'].create({'name': 'Outlet Closed'})
        cls.closed_session = cls.env['pos.session'].create({
            'user_id': cls.cashier.id,
            'config_id': closed_config.id,
        })
        cls.closed_session.write({'state': 'closed'})

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

    def set_language(self, code, session_id):
        return self.call_route(
            '/pos_bank_card_program/lang/set',
            {'code': code, 'session_id': session_id})

    def error_message(self, payload):
        return payload['error']['data']['message']

    def test_set_writes_user_language_and_session_context(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.set_language('id_ID', self.pos_session.id)
        self.assertEqual(payload['result'], 'id_ID')
        self.assertEqual(self.cashier.lang, 'id_ID')
        sid = self.opener.cookies.get('session_id')
        session = root.session_store.get(sid)
        self.assertEqual(session.context.get('lang'), 'id_ID')
        payload = self.set_language('en_US', self.pos_session.id)
        self.assertEqual(payload['result'], 'en_US')
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_set_works_for_a_cashier_joined_to_a_colleagues_session(self):
        self.authenticate('langjoiner', 'langjoiner')
        payload = self.set_language('id_ID', self.pos_session.id)
        self.assertEqual(payload['result'], 'id_ID')
        self.assertEqual(self.joiner.lang, 'id_ID')
        self.assertEqual(self.cashier.lang, 'en_US')
        self.set_language('en_US', self.pos_session.id)
        self.assertEqual(self.joiner.lang, 'en_US')

    def test_set_rejects_a_language_that_is_not_installed(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.set_language('su_ID', self.pos_session.id)
        self.assertIn('error', payload)
        self.assertIn('su_ID', self.error_message(payload))
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_set_rejects_a_code_that_is_not_a_string(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.set_language(['id_ID'], self.pos_session.id)
        self.assertIn('error', payload)
        self.assertIn('not installed', self.error_message(payload))
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_set_rejects_a_user_without_an_open_pos_session(self):
        self.authenticate('langoutsider', 'langoutsider')
        for params in (
            {'code': 'id_ID'},
            {'code': 'id_ID', 'session_id': 999999999},
            {'code': 'id_ID', 'session_id': 'open'},
        ):
            payload = self.call_route('/pos_bank_card_program/lang/set', params)
            self.assertIn('error', payload, params)
            self.assertIn('point of sale session', self.error_message(payload))
        self.assertEqual(self.outsider.lang, 'en_US')

    def test_set_rejects_a_closed_pos_session(self):
        self.authenticate('langcashier', 'langcashier')
        payload = self.set_language('id_ID', self.closed_session.id)
        self.assertIn('error', payload)
        self.assertIn('point of sale session', self.error_message(payload))
        self.assertEqual(self.cashier.lang, 'en_US')

    def test_set_cannot_change_another_users_language(self):
        self.authenticate('langcashier', 'langcashier')
        self.set_language('id_ID', self.pos_session.id)
        self.assertEqual(self.cashier.lang, 'id_ID')
        self.assertEqual(self.outsider.lang, 'en_US')
        self.set_language('en_US', self.pos_session.id)
