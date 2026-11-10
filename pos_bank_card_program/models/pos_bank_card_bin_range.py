from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .pos_bank_card_program import bin_interval


class PosBankCardBinRange(models.Model):
    _name = 'pos.bank.card.bin.range'
    _description = 'Bank Card BIN Range'
    _inherit = ['pos.load.mixin']
    _order = 'program_id, bin_from, id'

    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', related='program_id.company_id', store=True, readonly=True)
    bin_from = fields.Char(string='BIN From', required=True)
    bin_to = fields.Char(string='BIN To', required=True)
    bin_length = fields.Integer(string='BIN Length', compute='_compute_bin_length', store=True)
    network = fields.Char(string='Network')
    card_type = fields.Selection(
        selection=[('credit', 'Credit'), ('debit', 'Debit')], string='Card Type')
    tier = fields.Char(string='Tier')
    eligible = fields.Boolean(string='Eligible', default=True)
    ineligible_reason = fields.Char(string='Ineligible Reason', translate=True)

    @api.depends('bin_from')
    def _compute_bin_length(self):
        for record in self:
            record.bin_length = len(record.bin_from or '')

    @api.constrains('bin_from', 'bin_to', 'program_id')
    def _check_bin_range(self):
        for record in self:
            for value in (record.bin_from, record.bin_to):
                if not value or not value.isdigit() or len(value) not in (6, 8):
                    raise ValidationError(self.env._(
                        'A BIN must be 6 or 8 digits. Got %s.', value or ''))
            if len(record.bin_from) != len(record.bin_to):
                raise ValidationError(self.env._(
                    'The start and end of a BIN range must have the same length.'))
            if record.bin_from > record.bin_to:
                raise ValidationError(self.env._(
                    'The start of a BIN range must not be after its end.'))
            low, high = bin_interval(record.bin_from, record.bin_to)
            for other in record.program_id.bin_range_ids - record:
                if not other.bin_from or not other.bin_to:
                    continue
                if len(other.bin_from) not in (6, 8) or len(other.bin_to) not in (6, 8):
                    continue
                other_low, other_high = bin_interval(other.bin_from, other.bin_to)
                if low <= other_high and other_low <= high:
                    raise ValidationError(self.env._(
                        'BIN range %s to %s overlaps %s to %s in the same programme.',
                        record.bin_from, record.bin_to, other.bin_from, other.bin_to))

    @api.model
    def _load_pos_data_domain(self, data, config):
        day = fields.Date.context_today(self)
        return [
            ('program_id.state', '=', 'active'),
            ('program_id.active', '=', True),
            ('company_id', '=', config.company_id.id),
            '|', ('program_id.date_from', '=', False), ('program_id.date_from', '<=', day),
            '|', ('program_id.date_to', '=', False), ('program_id.date_to', '>=', day),
            '|', ('program_id.config_ids', '=', False), ('program_id.config_ids', 'in', [config.id]),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'program_id', 'bin_from', 'bin_to', 'bin_length', 'network',
            'card_type', 'tier', 'eligible', 'ineligible_reason',
        ]
