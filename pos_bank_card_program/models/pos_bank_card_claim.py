import base64
import io

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

MONEY_COLUMN_CODES = ('basis_amount', 'discount_amount', 'bank_share_amount', 'store_share_amount')


class PosBankCardClaim(models.Model):
    _name = 'pos.bank.card.claim'
    _description = 'Bank Card Claim'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Number', required=True, copy=False, default='/')
    bank_id = fields.Many2one(
        'res.partner', string='Bank', required=True, domain=[('is_company', '=', True)])
    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', check_company=True,
        domain="[('bank_id', '=', bank_id)]")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='Period From', required=True)
    date_to = fields.Date(string='Period To', required=True)
    usage_ids = fields.One2many('pos.bank.card.usage', 'claim_id', string='Claimed Usage')
    usage_count = fields.Integer(string='Usage Lines', compute='_compute_totals', store=True)
    total_discount_amount = fields.Monetary(
        string='Total Discount', currency_field='currency_id',
        compute='_compute_totals', store=True)
    total_amount = fields.Monetary(
        string='Total Bank Share', currency_field='currency_id',
        compute='_compute_totals', store=True)
    invoice_id = fields.Many2one('account.move', string='Invoice', check_company=True)
    export_file = fields.Binary(string='Excel Export')
    export_filename = fields.Char(string='Excel Filename')
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('paid', 'Paid'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, tracking=True)

    @api.depends('usage_ids.bank_share_amount', 'usage_ids.discount_amount', 'usage_ids.state')
    def _compute_totals(self):
        for claim in self:
            usages = claim.usage_ids.filtered(lambda usage: usage.state != 'cancelled')
            claim.usage_count = len(usages)
            claim.total_discount_amount = sum(usages.mapped('discount_amount'))
            claim.total_amount = sum(usages.mapped('bank_share_amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('pos.bank.card.claim') or '/'
        return super().create(vals_list)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for claim in self:
            if claim.date_to < claim.date_from:
                raise ValidationError(self.env._('The end date must not be before the start date.'))

    @api.constrains('bank_id', 'program_id')
    def _check_program_bank(self):
        for claim in self:
            if claim.program_id and claim.program_id.bank_id != claim.bank_id:
                raise ValidationError(self.env._(
                    'Programme %(programme)s belongs to %(bank)s, not to the claim bank.',
                    programme=claim.program_id.name,
                    bank=claim.program_id.bank_id.name))

    @api.constrains('bank_id', 'company_id', 'program_id', 'date_from', 'date_to', 'state')
    def _check_no_overlapping_claim(self):
        for claim in self:
            if claim.state in ('cancelled', 'rejected'):
                continue
            others = self.search([
                ('id', '!=', claim.id),
                ('bank_id', '=', claim.bank_id.id),
                ('company_id', '=', claim.company_id.id),
                ('state', 'not in', ('cancelled', 'rejected')),
                ('date_from', '<=', claim.date_to),
                ('date_to', '>=', claim.date_from),
            ])
            clash = others.filtered(
                lambda other: not other.program_id or not claim.program_id
                or other.program_id == claim.program_id)
            if clash:
                raise ValidationError(self.env._(
                    'Claim %(number)s already covers %(bank)s for an overlapping period '
                    '(%(date_from)s to %(date_to)s). One bank can only be claimed once per period.',
                    number=clash[0].name,
                    bank=claim.bank_id.name,
                    date_from=clash[0].date_from,
                    date_to=clash[0].date_to))

    def _billable_usages(self):
        self.ensure_one()
        return self.usage_ids.filtered(lambda usage: usage.state == 'confirmed')

    def _claim_columns_for(self, program):
        if program.claim_column_ids:
            return [(column.code, column.header) for column in program.claim_column_ids]
        return [
            (values['code'], values['header'])
            for _zero, _none, values in self.env['pos.bank.card.program']._default_claim_columns()
        ]

    def _column_value(self, usage, code):
        if code == 'date':
            return usage.date
        if code == 'order':
            return usage.pos_order_id.name or ''
        if code == 'outlet':
            return usage.config_id.name or ''
        if code == 'masked_card':
            return usage.masked_card or ''
        if code == 'approval_code':
            return usage.approval_code or ''
        if code == 'program':
            return usage.program_id.name or ''
        return usage[code] or 0.0

    def _build_workbook(self):
        import xlsxwriter

        self.ensure_one()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        header_format = workbook.add_format({'bold': True, 'border': 1})
        money_format = workbook.add_format({'num_format': '#,##0.00'})
        total_format = workbook.add_format({'num_format': '#,##0.00', 'bold': True, 'top': 1})
        total_label_format = workbook.add_format({'bold': True, 'top': 1})
        date_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm'})
        usages = self._billable_usages()
        programs = usages.mapped('program_id')
        if not programs:
            workbook.add_worksheet('Claim')
        used_sheet_names = set()
        for program in programs:
            sheet_name = (program.name or 'Claim')[:31]
            if sheet_name in used_sheet_names:
                sheet_name = ('%s %s' % (sheet_name[:27], program.id))[:31]
            used_sheet_names.add(sheet_name)
            sheet = workbook.add_worksheet(sheet_name)
            columns = self._claim_columns_for(program)
            for index, (_code, header) in enumerate(columns):
                sheet.write(0, index, header, header_format)
                sheet.set_column(index, index, 18)
            totals = dict.fromkeys(MONEY_COLUMN_CODES, 0.0)
            row = 1
            for usage in usages.filtered(lambda item: item.program_id == program):
                for index, (code, _header) in enumerate(columns):
                    value = self._column_value(usage, code)
                    if code == 'date':
                        sheet.write_datetime(row, index, value, date_format)
                    elif code in MONEY_COLUMN_CODES:
                        sheet.write_number(row, index, value, money_format)
                        totals[code] += value
                    else:
                        sheet.write_string(row, index, str(value))
                row += 1
            sheet.write_string(row, 0, self.env._('Total'), total_label_format)
            for index, (code, _header) in enumerate(columns):
                if code in MONEY_COLUMN_CODES:
                    sheet.write_number(row, index, totals[code], total_format)
        workbook.close()
        return output.getvalue()

    def action_export_xlsx(self):
        self.ensure_one()
        if not self._billable_usages():
            raise UserError(self.env._('This claim has no confirmed usage to export.'))
        content = self._build_workbook()
        filename = 'claim-%s.xlsx' % (self.name or '').replace('/', '-')
        self.write({
            'export_file': base64.b64encode(content),
            'export_filename': filename,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/pos.bank.card.claim/%s/export_file/%s?download=true'
                   % (self.id, filename),
            'target': 'self',
        }

    def _invoice_line_account(self, product):
        account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        if not account:
            raise UserError(self.env._(
                'No income account is configured for product %(product)s or its category. '
                'Set one before creating the claim invoice.',
                product=product.name))
        return account

    def action_create_invoice(self):
        self.ensure_one()
        if self.state in ('paid', 'rejected', 'cancelled'):
            raise UserError(self.env._('A %(state)s claim cannot be invoiced.', state=self.state))
        if self.invoice_id and self.invoice_id.state != 'cancel':
            raise UserError(self.env._(
                'This claim already has invoice %(invoice)s.', invoice=self.invoice_id.name or ''))
        usages = self._billable_usages()
        if not usages:
            raise UserError(self.env._('This claim has no confirmed usage to invoice.'))
        if self.currency_id.is_zero(self.total_amount):
            raise UserError(self.env._('The total bank share is zero, so there is nothing to invoice.'))
        product = self.env.ref('pos_bank_card_program.product_bank_card_claim_template').product_variant_id
        account = self._invoice_line_account(product)
        lines = []
        for program in usages.mapped('program_id'):
            amount = sum(usages.filtered(
                lambda usage: usage.program_id == program).mapped('bank_share_amount'))
            if self.currency_id.is_zero(amount):
                continue
            lines.append((0, 0, {
                'product_id': product.id,
                'name': self.env._(
                    'Bank card claim %(claim)s: %(programme)s (%(date_from)s to %(date_to)s)',
                    claim=self.name, programme=program.name,
                    date_from=self.date_from, date_to=self.date_to),
                'quantity': 1.0,
                'price_unit': amount,
                'account_id': account.id,
                'tax_ids': [(6, 0, [])],
            }))
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.bank_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': self.name,
            'ref': self.name,
            'invoice_line_ids': lines,
        })
        self.write({'invoice_id': invoice.id})
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Claim Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
        }

    def action_open_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Claim Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def action_open_usages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Claimed Usage'),
            'res_model': 'pos.bank.card.usage',
            'view_mode': 'list,pivot,graph,form',
            'domain': [('claim_id', '=', self.id)],
        }

    def action_send(self):
        for claim in self:
            if claim.state != 'draft':
                raise UserError(self.env._('Only a draft claim can be sent.'))
            if not claim._billable_usages():
                raise UserError(self.env._('This claim has no confirmed usage to send.'))
            claim.state = 'sent'

    def _mark_paid(self):
        self.ensure_one()
        self.state = 'paid'
        self._billable_usages().filtered(
            lambda usage: usage.claim_state == 'claimed').write({'claim_state': 'paid'})

    def action_reconcile(self):
        for claim in self:
            if claim.state != 'sent':
                raise UserError(self.env._('Only a sent claim can be reconciled.'))
            if not claim.invoice_id:
                raise UserError(self.env._(
                    'There is no invoice to reconcile against. Create the invoice, '
                    'or use Mark Paid when the bank paid without one.'))
            if claim.invoice_id.payment_state not in ('paid', 'in_payment'):
                raise UserError(self.env._(
                    'Invoice %(invoice)s is not paid yet, so the claim stays outstanding.',
                    invoice=claim.invoice_id.name or ''))
            claim._mark_paid()

    def action_mark_paid(self):
        for claim in self:
            if claim.state != 'sent':
                raise UserError(self.env._('Only a sent claim can be marked paid.'))
            if claim.invoice_id and claim.invoice_id.state != 'cancel':
                raise UserError(self.env._(
                    'This claim has an invoice. Register its payment and use Reconcile '
                    'so the claim follows the invoice.'))
            claim._mark_paid()

    def _release_usages(self):
        self.ensure_one()
        usages = self.usage_ids.filtered(lambda usage: usage.claim_state == 'claimed')
        usages.write({'claim_state': 'not_claimed', 'claim_id': False})
        return usages

    def _check_invoice_allows_release(self):
        self.ensure_one()
        if self.invoice_id and self.invoice_id.state != 'cancel':
            raise UserError(self.env._(
                'Cancel invoice %(invoice)s before releasing this claim.',
                invoice=self.invoice_id.name or ''))

    def action_reject(self):
        for claim in self:
            if claim.state != 'sent':
                raise UserError(self.env._('Only a sent claim can be recorded as rejected.'))
            claim._check_invoice_allows_release()
            released_total = claim.total_amount
            released = claim._release_usages()
            claim.state = 'rejected'
            claim.message_post(body=self.env._(
                'The bank rejected this claim. %(count)s usage lines totalling %(amount)s '
                'were released and can be claimed again.',
                count=len(released), amount=released_total))

    def action_cancel(self):
        for claim in self:
            if claim.state == 'paid':
                raise UserError(self.env._('A paid claim cannot be cancelled.'))
            claim._check_invoice_allows_release()
            released_total = claim.total_amount
            released = claim._release_usages()
            claim.state = 'cancelled'
            claim.message_post(body=self.env._(
                'The claim was cancelled. %(count)s usage lines totalling %(amount)s '
                'were released and can be claimed again.',
                count=len(released), amount=released_total))


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
