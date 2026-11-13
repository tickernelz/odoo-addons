from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def action_pos_order_paid(self):
        self._fill_statement_memo_partner()
        self.payment_ids._bind_xendit_qris_requests()
        return super().action_pos_order_paid()

    def _fill_statement_memo_partner(self):
        for order in self.filtered(lambda o: not o.partner_id):
            partners = order.payment_ids.payment_method_id.filtered('split_transactions').statement_memo_partner_id
            if len(partners) == 1:
                order.partner_id = partners
