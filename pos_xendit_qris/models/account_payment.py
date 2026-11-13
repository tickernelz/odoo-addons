from odoo import api, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.model_create_multi
    def create(self, vals_list):
        memo = self.env.context.get('pos_statement_memo')
        if memo:
            for vals in vals_list:
                if vals.get('pos_session_id'):
                    vals['memo'] = memo
        return super().create(vals_list)
