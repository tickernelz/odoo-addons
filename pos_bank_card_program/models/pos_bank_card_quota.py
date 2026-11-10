from odoo import api, fields, models


class PosBankCardQuota(models.Model):
    _name = 'pos.bank.card.quota'
    _description = 'Bank Card Programme Quota'
    _order = 'date_from desc, id desc'

    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', related='program_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='program_id.currency_id', readonly=True)
    date_from = fields.Date(string='Period From', required=True)
    date_to = fields.Date(string='Period To', required=True)
    used_count = fields.Integer(string='Used (Transactions)', default=0, readonly=True)
    used_amount = fields.Monetary(
        string='Used (Amount)', currency_field='currency_id', default=0.0, readonly=True)
    remaining_count = fields.Integer(string='Remaining (Transactions)', compute='_compute_remaining')
    remaining_amount = fields.Monetary(
        string='Remaining (Amount)', currency_field='currency_id', compute='_compute_remaining')

    _unique_bucket = models.Constraint(
        'UNIQUE(program_id, date_from, date_to)',
        'There is already a quota bucket for this programme and period.')

    @api.depends('used_count', 'used_amount', 'program_id.quota_limit_count', 'program_id.quota_limit_amount')
    def _compute_remaining(self):
        for quota in self:
            quota.remaining_count = max(quota.program_id.quota_limit_count - quota.used_count, 0)
            quota.remaining_amount = max(quota.program_id.quota_limit_amount - quota.used_amount, 0.0)


class PosBankCardCardlimit(models.Model):
    _name = 'pos.bank.card.cardlimit'
    _description = 'Bank Card Daily Use per Card'
    _order = 'date desc, id desc'

    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', related='program_id.company_id', store=True, readonly=True)
    card_bin = fields.Char(string='Card BIN', required=True, index=True)
    card_last4 = fields.Char(string='Last 4 Digits', required=True, index=True)
    date = fields.Date(
        string='Date', required=True, default=lambda self: fields.Date.context_today(self))
    used_count = fields.Integer(string='Used', default=0, readonly=True)

    _unique_card_day = models.Constraint(
        'UNIQUE(program_id, card_bin, card_last4, date)',
        'There is already a daily counter for this card in this programme.')
