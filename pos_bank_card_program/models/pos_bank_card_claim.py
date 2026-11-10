from odoo import api, fields, models


class PosBankCardClaim(models.Model):
    _name = 'pos.bank.card.claim'
    _description = 'Bank Card Claim'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Number', required=True, copy=False, default='/')
    bank_id = fields.Many2one(
        'res.partner', string='Bank', required=True, domain=[('is_company', '=', True)])
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='Period From', required=True)
    date_to = fields.Date(string='Period To', required=True)
    usage_ids = fields.One2many('pos.bank.card.usage', 'claim_id', string='Claimed Usage')
    total_amount = fields.Monetary(
        string='Total Bank Share', currency_field='currency_id',
        compute='_compute_total_amount', store=True)
    invoice_id = fields.Many2one('account.move', string='Invoice', check_company=True)
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('paid', 'Paid'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, tracking=True)

    @api.depends('usage_ids.bank_share_amount', 'usage_ids.state')
    def _compute_total_amount(self):
        for claim in self:
            claim.total_amount = sum(
                claim.usage_ids.filtered(lambda usage: usage.state != 'cancelled').mapped('bank_share_amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('pos.bank.card.claim') or '/'
        return super().create(vals_list)


class PosBankCardClaimColumn(models.Model):
    _name = 'pos.bank.card.claim.column'
    _description = 'Bank Card Claim Export Column'
    _order = 'sequence, id'

    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Sequence', default=10)
    code = fields.Selection(
        selection=[
            ('date', 'Date'),
            ('order', 'Order'),
            ('outlet', 'Outlet'),
            ('masked_card', 'Card'),
            ('approval_code', 'Approval Code'),
            ('basis_amount', 'Basis'),
            ('discount_amount', 'Discount'),
            ('bank_share_amount', 'Bank Share'),
            ('store_share_amount', 'Store Share'),
            ('program', 'Programme'),
        ],
        string='Value', required=True)
    header = fields.Char(string='Header', required=True)
