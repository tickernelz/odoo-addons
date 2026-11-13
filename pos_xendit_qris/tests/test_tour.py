from odoo.tests import tagged

from .common import XenditQrisCase


@tagged('post_install', '-at_install')
class TestXenditQrisTour(XenditQrisCase):

    def test_cashier_shows_the_qris_and_the_webhook_completes_the_sale(self):
        self.main_pos_config.auto_validate_terminal_payment = True
        self.main_pos_config.with_user(self.pos_user).open_ui()
        with self.fake_xendit() as fake:
            self.start_pos_tour('pos_xendit_qris_pay')
        self.assertEqual(len(fake.calls), 1)
        request = self.env['pos.xendit.qris.request'].search([])
        self.assertEqual(request.state, 'paid')
        order = self.env['pos.order'].search([('session_id', '=', self.main_pos_config.current_session_id.id)])
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.payment_ids.transaction_id, request.reference_id)
        self.assertEqual(order.partner_id, self.walk_in)
