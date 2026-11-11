from odoo import api, fields, models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    kai_exclusive_group = fields.Char(
        string='Exclusive Group',
        help='Within one group only the largest discount applies to an order, POS promotions included.')
    kai_priority = fields.Integer(string='Group Priority', default=10, help='On equal discounts the lower number wins.')
    kai_happy_hour = fields.Boolean(string='Happy Hour', help='Only apply the programme inside the windows below, in the timezone of the point of sale.')
    kai_schedule_ids = fields.One2many('kai.pos.promo.schedule', 'program_id', string='Happy Hour Windows')

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ['kai_exclusive_group', 'kai_priority', 'kai_happy_hour', 'kai_schedule_ids']
