from odoo import api, fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    card_bin = fields.Char(string='Card BIN', size=8)
    card_tier = fields.Char(string='Card Tier')
    bank_card_program_id = fields.Many2one('pos.bank.card.program', string='Bank Card Programme')
    bank_card_discount = fields.Monetary(string='Bank Card Discount', currency_field='currency_id', default=0.0)
    bank_card_bank_share = fields.Monetary(string='Bank Share', currency_field='currency_id', default=0.0)
    bank_card_claim_state = fields.Selection(
        selection=[
            ('not_claimed', 'Not Claimed'),
            ('claimed', 'Claimed'),
            ('paid', 'Paid'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Bank Card Claim Status', default='not_claimed')

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + [
            'card_bin', 'card_tier', 'bank_card_program_id',
            'bank_card_discount', 'bank_card_bank_share', 'bank_card_claim_state',
        ]


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    bank_card_required = fields.Boolean(string='Requires Bank Card Info', default=False)
    bank_card_program_id = fields.Many2one('pos.bank.card.program', string='Default Bank Card Programme')

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + [
            'bank_card_required', 'bank_card_program_id',
        ]
