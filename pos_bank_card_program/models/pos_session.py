from odoo import fields, models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _load_pos_data_models(self, config):
        return super()._load_pos_data_models(config) + [
            'pos.bank.card.program', 'pos.bank.card.bin.range',
        ]

    def _create_account_move(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        result = super()._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)
        self._reclassify_bank_card_shares()
        return result

    def _reclassify_bank_card_shares(self):
        self.ensure_one()
        Usage = self.env['pos.bank.card.usage'].sudo()
        usages = Usage.search([
            ('session_id', '=', self.id),
            ('state', '=', 'confirmed'),
            ('reclass_move_id', '=', False),
            ('bank_share_amount', '>', 0),
        ])
        company_currency = self.company_id.currency_id
        for program in usages.mapped('program_id'):
            program_usages = usages.filtered(lambda usage: usage.program_id == program)
            if not (program.receivable_account_id and program.discount_account_id):
                continue
            amount = sum(program_usages.mapped('bank_share_amount'))
            if company_currency.is_zero(amount):
                continue
            journal = program.journal_id or self.config_id.journal_id
            name = self.env._('Bank card claim reclassification %s', program.name)
            move = self.env['account.move'].sudo().create({
                'journal_id': journal.id,
                'date': fields.Date.context_today(self),
                'ref': self.name,
                'company_id': self.company_id.id,
                'line_ids': [
                    (0, 0, {
                        'name': name,
                        'account_id': program.receivable_account_id.id,
                        'partner_id': program.bank_id.id,
                        'debit': amount,
                        'credit': 0.0,
                    }),
                    (0, 0, {
                        'name': name,
                        'account_id': program.discount_account_id.id,
                        'debit': 0.0,
                        'credit': amount,
                    }),
                ],
            })
            move.action_post()
            program_usages.write({'reclass_move_id': move.id})
