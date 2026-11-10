from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    bank_card_program_id = fields.Many2one('pos.bank.card.program', string='Bank Card Programme')
    bank_card_masked = fields.Char(string='Bank Card')
    bank_card_reservation_state = fields.Selection(
        selection=[
            ('none', 'None'),
            ('reserved', 'Reserved'),
            ('confirmed', 'Confirmed'),
            ('released', 'Released'),
        ],
        string='Bank Card Reservation', default='none')
    bank_card_usage_id = fields.Many2one('pos.bank.card.usage', string='Bank Card Usage')

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + [
            'bank_card_program_id', 'bank_card_masked',
            'bank_card_reservation_state', 'bank_card_usage_id',
        ]
