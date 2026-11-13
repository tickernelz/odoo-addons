from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import XenditQrisCase


@tagged('post_install', '-at_install')
class TestXenditQrisAccess(XenditQrisCase):

    def test_cashier_and_manager_cannot_read_the_secrets(self):
        for user in (self.pos_user, self.pos_admin):
            method = self.qris.with_user(user)
            with self.subTest(user=user.login):
                for field in ('xendit_qris_secret_key', 'xendit_qris_webhook_token'):
                    with self.assertRaises(AccessError):
                        method.read([field])

    def test_secrets_never_reach_the_till(self):
        fields = self.env['pos.payment.method']._load_pos_data_fields(self.main_pos_config)
        self.assertNotIn('xendit_qris_secret_key', fields)
        self.assertNotIn('xendit_qris_webhook_token', fields)
        self.assertIn('statement_memo_partner_id', fields)

    def test_cashier_reads_requests_but_cannot_change_them(self):
        request, _data, _fake = self.create_qris()
        as_cashier = request.with_user(self.pos_user)
        self.assertEqual(as_cashier.state, 'pending')
        with self.assertRaises(AccessError):
            as_cashier.write({'state': 'paid'})
        with self.assertRaises(AccessError):
            as_cashier.unlink()
        with self.assertRaises(AccessError):
            self.env['pos.xendit.qris.request'].with_user(self.pos_user).create({
                'payment_method_id': self.qris.id, 'session_id': request.session_id.id,
                'currency_id': request.currency_id.id, 'amount': 1})

    def test_webhook_url_carries_no_secret(self):
        self.assertTrue(self.qris.xendit_qris_webhook_url.endswith('/pos_xendit_qris/webhook'))
        self.assertNotIn('webhook-token', self.qris.xendit_qris_webhook_url)
