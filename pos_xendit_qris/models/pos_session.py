from odoo import models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_split_account_payment_vals(self, payment, amounts, accounting_partner, destination_account):
        vals = super()._get_split_account_payment_vals(payment, amounts, accounting_partner, destination_account)
        memo = payment._statement_memo()
        if memo:
            vals['memo'] = memo
        return vals

    def _create_split_account_payment(self, payment, amounts):
        memo = payment._statement_memo()
        session = self.with_context(pos_statement_memo=memo) if memo else self
        return super(PosSession, session)._create_split_account_payment(payment, amounts)
