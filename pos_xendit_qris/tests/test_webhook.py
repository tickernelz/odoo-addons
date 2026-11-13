from unittest.mock import patch

from odoo.tests import tagged

from .common import XenditQrisCase, capture_payload


@tagged('post_install', '-at_install')
class TestXenditQrisWebhook(XenditQrisCase):

    def setUp(self):
        super().setUp()
        self.request, _data, _fake = self.create_qris(amount=50000.0)

    def assertStays(self, state):
        self.request.invalidate_recordset()
        self.assertEqual(self.request.state, state)

    def test_capture_with_the_token_marks_paid_and_tells_the_till(self):
        with patch.object(type(self.main_pos_config), '_notify') as notify:
            response = self.post_webhook(capture_payload(self.request))
        self.assertEqual(response.status_code, 200)
        self.assertStays('paid')
        self.assertEqual(self.request.payment_id, 'py-0001')
        notify.assert_called_once_with('XENDIT_QRIS', {'request_id': self.request.id, 'state': 'paid'})

    def test_missing_token_is_refused(self):
        self.assertEqual(self.post_webhook(capture_payload(self.request), token=None).status_code, 403)
        self.assertStays('pending')

    def test_wrong_token_is_refused(self):
        self.assertEqual(self.post_webhook(capture_payload(self.request), token='guess').status_code, 403)
        self.assertStays('pending')

    def test_token_of_another_method_is_refused(self):
        self.env['pos.payment.method'].create({
            'name': 'Other QRIS', 'journal_id': self.bank_journal.id, 'payment_method_type': 'terminal',
            'use_payment_terminal': 'xendit_qris', 'xendit_qris_secret_key': 'k',
            'xendit_qris_webhook_token': 'other-method-token'})
        self.assertEqual(self.post_webhook(capture_payload(self.request), token='other-method-token').status_code, 403)
        self.assertStays('pending')

    def test_unknown_reference_answers_200_and_changes_nothing(self):
        payload = capture_payload(self.request)
        payload['data']['reference_id'] = 'not-ours-123'
        self.assertEqual(self.post_webhook(payload).status_code, 200)
        self.assertStays('pending')

    def test_malformed_body_is_refused(self):
        self.assertEqual(self.post_webhook(None, raw='{not json').status_code, 400)

    def test_other_amount_does_not_mark_paid(self):
        self.assertEqual(self.post_webhook(capture_payload(self.request, amount=500)).status_code, 200)
        self.assertStays('pending')
        self.assertIn('500', self.request.attention)

    def test_other_currency_does_not_mark_paid(self):
        self.post_webhook(capture_payload(self.request, currency='PHP'))
        self.assertStays('pending')

    def test_other_payment_request_does_not_mark_paid(self):
        payload = capture_payload(self.request)
        payload['data']['payment_request_id'] = 'pr-forged'
        self.post_webhook(payload)
        self.assertStays('pending')

    def test_repeated_capture_marks_paid_once(self):
        self.post_webhook(capture_payload(self.request))
        self.request.invalidate_recordset()
        paid_at = self.request.paid_at
        with patch.object(type(self.main_pos_config), '_notify') as notify:
            self.assertEqual(self.post_webhook(capture_payload(self.request)).status_code, 200)
        notify.assert_not_called()
        self.assertStays('paid')
        self.assertEqual(self.request.paid_at, paid_at)
        self.assertFalse(self.request.attention)

    def test_capture_after_cancel_is_flagged_not_paid(self):
        self.request.state = 'cancelled'
        self.post_webhook(capture_payload(self.request))
        self.assertStays('cancelled')
        self.assertIn('settle it by hand', self.request.attention)

    def test_expiry_and_failure_close_a_pending_request(self):
        self.post_webhook(capture_payload(self.request, event='payment_request.expiry', status='EXPIRED'))
        self.assertStays('expired')
        other, _data, _fake = self.create_qris(amount=30000.0)
        self.post_webhook(capture_payload(other, event='payment.failure', status='FAILED'))
        other.invalidate_recordset()
        self.assertEqual(other.state, 'failed')
