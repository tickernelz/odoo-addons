from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _process_saved_order(self, draft):
        if not draft and self.state != 'cancel':
            self._check_xendit_qris_paid()
        return super()._process_saved_order(draft)

    def action_pos_order_paid(self):
        self._check_xendit_qris_paid()
        return super().action_pos_order_paid()

    def _check_xendit_qris_paid(self):
        """Fill the default customer and refuse Xendit QRIS payments that Xendit has not paid."""
        self._fill_statement_memo_partner()
        self.payment_ids._bind_xendit_qris_requests()

    def _fill_statement_memo_partner(self):
        for order in self.filtered(lambda o: not o.partner_id):
            partners = order.payment_ids.payment_method_id.filtered('split_transactions').statement_memo_partner_id
            if len(partners) == 1:
                order.partner_id = partners
