from datetime import datetime, time, timedelta, timezone

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError


class PosBankCardClaimWizard(models.TransientModel):
    _name = 'pos.bank.card.claim.wizard'
    _description = 'Bank Card Claim Wizard'

    bank_id = fields.Many2one(
        'res.partner', string='Bank', required=True, domain=[('is_company', '=', True)])
    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme',
        domain="[('bank_id', '=', bank_id), ('company_id', '=', company_id)]")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(
        string='Period From', required=True, default=lambda self: self._default_date_from())
    date_to = fields.Date(
        string='Period To', required=True, default=lambda self: self._default_date_to())
    state = fields.Selection(
        selection=[('select', 'Select'), ('preview', 'Preview')],
        string='Step', default='select', required=True)
    line_ids = fields.One2many(
        'pos.bank.card.claim.wizard.line', 'wizard_id', string='Per Outlet')
    usage_count = fields.Integer(string='Usage Lines', compute='_compute_totals')
    total_basis_amount = fields.Monetary(
        string='Total Basis', currency_field='currency_id', compute='_compute_totals')
    total_discount_amount = fields.Monetary(
        string='Total Discount', currency_field='currency_id', compute='_compute_totals')
    total_bank_share_amount = fields.Monetary(
        string='Total Bank Share', currency_field='currency_id', compute='_compute_totals')

    @api.model
    def _default_date_from(self):
        return self._oldest_unclaimed_day([('company_id', '=', self.env.company.id)])

    @api.model
    def _default_date_to(self):
        return fields.Date.context_today(self)

    @api.model
    def _oldest_unclaimed_day(self, domain):
        today = fields.Date.context_today(self)
        oldest = self.env['pos.bank.card.usage'].search(
            domain + [('state', '=', 'confirmed'), ('claim_state', '=', 'not_claimed')],
            order='date, id', limit=1)
        if not oldest:
            return today.replace(day=1)
        return min(fields.Date.context_today(self, oldest.date), today)

    @api.depends(
        'line_ids.usage_count', 'line_ids.basis_amount',
        'line_ids.discount_amount', 'line_ids.bank_share_amount')
    def _compute_totals(self):
        for wizard in self:
            wizard.usage_count = sum(wizard.line_ids.mapped('usage_count'))
            wizard.total_basis_amount = sum(wizard.line_ids.mapped('basis_amount'))
            wizard.total_discount_amount = sum(wizard.line_ids.mapped('discount_amount'))
            wizard.total_bank_share_amount = sum(wizard.line_ids.mapped('bank_share_amount'))

    @api.onchange('bank_id')
    def _onchange_bank_id(self):
        if self.program_id and self.program_id.bank_id != self.bank_id:
            self.program_id = False

    @api.onchange('bank_id', 'program_id', 'company_id')
    def _onchange_period(self):
        if not self.bank_id or self.state != 'select':
            return
        domain = [('bank_id', '=', self.bank_id.id), ('company_id', '=', self.company_id.id)]
        if self.program_id:
            domain.append(('program_id', '=', self.program_id.id))
        self.date_from = self._oldest_unclaimed_day(domain)
        self.date_to = fields.Date.context_today(self)

    def _period_bounds_utc(self):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        start = tz.localize(datetime.combine(self.date_from, time.min))
        end = tz.localize(datetime.combine(self.date_to + timedelta(days=1), time.min))
        return (
            start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))

    def _usage_domain(self):
        self.ensure_one()
        date_from, date_to = self._period_bounds_utc()
        domain = [
            ('bank_id', '=', self.bank_id.id),
            ('company_id', '=', self.company_id.id),
            ('state', '=', 'confirmed'),
            ('claim_state', '=', 'not_claimed'),
            ('date', '>=', date_from),
            ('date', '<', date_to),
        ]
        if self.program_id:
            domain.append(('program_id', '=', self.program_id.id))
        return domain

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Claim to Bank'),
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_preview(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._('The end date must not be before the start date.'))
        usages = self.env['pos.bank.card.usage'].search(self._usage_domain())
        data = self.env['pos.bank.card.claim']._claim_report_data(usages, details=False)
        lines = [(5, 0, 0)] + [(0, 0, {
            'config_id': line['config_id'].id or False,
            'usage_count': line['usage_count'],
            'basis_amount': line['basis_amount'],
            'discount_amount': line['discount_amount'],
            'bank_share_amount': line['bank_share_amount'],
        }) for line in data['summary']]
        self.write({'line_ids': lines, 'state': 'preview'})
        return self._reopen()

    def _check_printable(self):
        self.ensure_one()
        self.env['pos.bank.card.claim']._check_claim_pdf_access()
        if not self.env['pos.bank.card.usage'].search_count(self._usage_domain(), limit=1):
            raise UserError(self.env._(
                'No unclaimed confirmed usage matches this bank, programme and period.'))

    def _claim_pdf_data(self):
        self._check_printable()
        usages = self.env['pos.bank.card.usage'].search(self._usage_domain(), order='date, id')
        return self.env['pos.bank.card.claim']._claim_pdf_values(
            usages, self.bank_id, self.program_id, self.company_id,
            self.date_from, self.date_to, False)

    def action_print_pdf(self):
        self._check_printable()
        return {
            'type': 'ir.actions.act_url',
            'url': '/report/pdf/pos_bank_card_program.report_bank_card_claim_wizard/%s' % self.id,
            'target': 'new',
        }

    def action_back(self):
        self.ensure_one()
        self.state = 'select'
        return self._reopen()

    def action_create_claim(self):
        self.ensure_one()
        usages = self.env['pos.bank.card.usage'].search(self._usage_domain(), order='id')
        if not usages:
            raise UserError(self.env._(
                'No unclaimed confirmed usage matches this bank, programme and period.'))
        self.env.cr.execute(
            'SELECT id FROM pos_bank_card_usage WHERE id IN %s FOR UPDATE',
            (tuple(usages.ids),))
        usages.invalidate_recordset(['claim_state', 'claim_id'])
        usages = usages.filtered(lambda usage: usage.claim_state == 'not_claimed' and not usage.claim_id)
        if not usages:
            raise UserError(self.env._(
                'No unclaimed confirmed usage matches this bank, programme and period.'))
        claim = self.env['pos.bank.card.claim'].create({
            'bank_id': self.bank_id.id,
            'program_id': self.program_id.id if self.program_id else False,
            'company_id': self.company_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        usages.write({'claim_id': claim.id, 'claim_state': 'claimed'})
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Bank Card Claim'),
            'res_model': 'pos.bank.card.claim',
            'view_mode': 'form',
            'res_id': claim.id,
        }


class PosBankCardClaimWizardLine(models.TransientModel):
    _name = 'pos.bank.card.claim.wizard.line'
    _description = 'Bank Card Claim Wizard Line'
    _order = 'config_id, id'

    wizard_id = fields.Many2one(
        'pos.bank.card.claim.wizard', string='Wizard', required=True, ondelete='cascade')
    config_id = fields.Many2one('pos.config', string='Outlet', readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='wizard_id.currency_id', readonly=True)
    usage_count = fields.Integer(string='Usage Lines', readonly=True)
    basis_amount = fields.Monetary(
        string='Basis', currency_field='currency_id', readonly=True)
    discount_amount = fields.Monetary(
        string='Discount', currency_field='currency_id', readonly=True)
    bank_share_amount = fields.Monetary(
        string='Bank Share', currency_field='currency_id', readonly=True)
