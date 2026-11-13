from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import TIMEOUT_ERROR, FakeResponse, XenditQrisCase, status_answer


@tagged('post_install', '-at_install')
class TestXenditQrisRequest(XenditQrisCase):

    def test_create_sends_one_v3_request_and_returns_the_qris_image(self):
        request, data, fake = self.create_qris(order_reference='Order 2610-1-0007')
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual((call['method'], call['url']), ('POST', 'https://api.xendit.co/v3/payment_requests'))
        self.assertEqual(call['headers']['api-version'], '2024-11-11')
        self.assertEqual(call['auth'], ('xnd_development_secret', ''))
        self.assertEqual(call['timeout'], (5, 15))
        self.assertEqual(call['json']['channel_code'], 'QRIS')
        self.assertEqual(call['json']['request_amount'], 25000)
        self.assertEqual(call['json']['reference_id'], 'Order2610-1-0007-%s' % request.id)
        self.assertEqual(request.state, 'pending')
        self.assertEqual(data['reference'], request.reference_id)
        self.assertTrue(data['qr_image'].startswith('data:image/png;base64,iVBOR'))
        self.assertNotIn('qr_string', data)

    def test_repeating_the_same_payment_line_reuses_the_pending_qris(self):
        request, data, _fake = self.create_qris(payment_uuid='line-1')
        with self.fake_xendit() as fake:
            again = self.qris.with_user(self.pos_user).xendit_qris_create(
                self.open_session().id, 'order-line-1', 'line-1', 25000.0, '2610-1-0001')
        self.assertEqual(fake.calls, [])
        self.assertEqual(again['id'], request.id)

    def test_timeout_reaches_the_cashier_and_leaves_no_request(self):
        session = self.open_session()
        with self.fake_xendit(TIMEOUT_ERROR), self.assertRaisesRegex(UserError, 'read timed out'), self.env.cr.savepoint():
            self.qris.with_user(self.pos_user).xendit_qris_create(session.id, 'o', 'line-timeout', 25000.0, 'R')
        self.assertFalse(self.env['pos.xendit.qris.request'].search([('pos_payment_uuid', '=', 'line-timeout')]))

    def test_xendit_refusal_shows_its_message(self):
        session = self.open_session()
        refusal = FakeResponse(401, {'error_code': 'INVALID_API_KEY', 'message': 'API key is invalid'})
        with self.fake_xendit(refusal), self.assertRaisesRegex(UserError, 'API key is invalid'):
            self.qris.with_user(self.pos_user).xendit_qris_create(session.id, 'o', 'line-401', 25000.0, 'R')

    def test_amount_rules(self):
        session = self.open_session()
        method = self.qris.with_user(self.pos_user)
        for amount in (0, -5000, 1500.5, 10_000_001):
            with self.subTest(amount=amount), self.fake_xendit() as fake, self.assertRaises(UserError):
                method.xendit_qris_create(session.id, 'o', 'line-amount', amount, 'R')
            self.assertEqual(fake.calls, [])

    def test_only_point_of_sale_users_of_the_open_session_can_create(self):
        session = self.open_session()
        outsider = self.env['res.users'].create({'name': 'Outsider', 'login': 'outsider-qris'})
        with self.fake_xendit() as fake, self.assertRaises(AccessError):
            self.qris.with_user(outsider).xendit_qris_create(session.id, 'o', 'line-x', 25000.0, 'R')
        other = self.env['pos.payment.method'].create({
            'name': 'Other QRIS', 'journal_id': self.bank_journal.id, 'payment_method_type': 'terminal',
            'use_payment_terminal': 'xendit_qris', 'xendit_qris_secret_key': 'k', 'xendit_qris_webhook_token': 't'})
        with self.fake_xendit() as fake, self.assertRaisesRegex(UserError, 'not open'):
            other.with_user(self.pos_user).xendit_qris_create(session.id, 'o', 'line-y', 25000.0, 'R')
        self.assertEqual(fake.calls, [])

    def test_status_asks_xendit_and_marks_paid(self):
        request, _data, _fake = self.create_qris()
        with self.fake_xendit(status_answer(request, 'SUCCEEDED', payment_id='py-77')):
            status = self.qris.with_user(self.pos_user).xendit_qris_status(request.id, True)
        self.assertEqual(status['state'], 'paid')
        self.assertEqual(request.payment_id, 'py-77')

    def test_status_with_another_amount_stays_pending(self):
        request, _data, _fake = self.create_qris()
        with self.fake_xendit(status_answer(request, 'SUCCEEDED', amount=1000)):
            status = self.qris.with_user(self.pos_user).xendit_qris_status(request.id, True)
        self.assertEqual(status['state'], 'pending')
        self.assertTrue(request.attention)

    def test_cancel_closes_the_request(self):
        request, _data, _fake = self.create_qris()
        with self.fake_xendit(status_answer(request, 'CANCELED')) as fake:
            status = self.qris.with_user(self.pos_user).xendit_qris_cancel(request.id)
        self.assertTrue(fake.calls[0]['url'].endswith('/cancel'))
        self.assertEqual(status['state'], 'cancelled')

    def test_cancel_refused_because_already_paid_keeps_it_paid(self):
        request, _data, _fake = self.create_qris()
        refusal = FakeResponse(409, {'error_code': 'INVALID_STATE', 'message': 'already succeeded'})
        with self.fake_xendit(refusal, status_answer(request, 'SUCCEEDED')):
            status = self.qris.with_user(self.pos_user).xendit_qris_cancel(request.id)
        self.assertEqual(status['state'], 'paid')

    def test_cancel_that_cannot_reach_xendit_keeps_waiting(self):
        request, _data, _fake = self.create_qris()
        with self.fake_xendit(TIMEOUT_ERROR, TIMEOUT_ERROR), self.assertRaises(UserError):
            self.qris.with_user(self.pos_user).xendit_qris_cancel(request.id)
        self.assertEqual(request.state, 'pending')
