import datetime

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import ConcurrencyError, ValidationError
from odoo.tools import format_amount, format_date


def bin_interval(value_from, value_to):
    factor = 100 if len(value_from) == 6 else 1
    return int(value_from) * factor, int(value_to) * factor + factor - 1


class PosBankCardProgram(models.Model):
    _name = 'pos.bank.card.program'
    _description = 'Bank Card Programme'
    _inherit = ['pos.load.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(string='Name', required=True, tracking=True)
    active = fields.Boolean(string='Active', default=True)
    state = fields.Selection(
        selection=[('draft', 'Draft'), ('active', 'Active'), ('closed', 'Closed')],
        string='Status', default='draft', required=True, tracking=True, copy=False)
    bank_id = fields.Many2one(
        'res.partner', string='Bank', required=True, tracking=True,
        domain=[('is_company', '=', True)])
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='Valid From', tracking=True)
    date_to = fields.Date(string='Valid To', tracking=True)
    mode = fields.Selection(
        selection=[('discount', 'Immediate Discount'), ('marker', 'Marker (Bank Cashback)')],
        string='Mode', default='discount', required=True)
    config_ids = fields.Many2many('pos.config', string='Outlets', check_company=True)
    payment_method_ids = fields.Many2many(
        'pos.payment.method', string='Payment Methods', check_company=True)
    bin_range_ids = fields.One2many('pos.bank.card.bin.range', 'program_id', string='BIN Ranges')
    usage_ids = fields.One2many('pos.bank.card.usage', 'program_id', string='Usage')
    quota_ids = fields.One2many('pos.bank.card.quota', 'program_id', string='Quota Buckets')
    discount_type = fields.Selection(
        selection=[('percent', 'Percentage'), ('fixed', 'Fixed Amount')],
        string='Discount Type', default='percent', required=True)
    discount_percent = fields.Float(string='Discount %', default=0.0)
    discount_fixed_amount = fields.Monetary(
        string='Fixed Discount Amount', currency_field='currency_id', default=0.0)
    discount_basis = fields.Selection(
        selection=[
            ('subtotal', 'Order Subtotal'),
            ('total', 'Order Total'),
            ('card_amount', 'Card Payment Amount'),
        ],
        string='Discount Basis', default='subtotal', required=True)
    promo_order = fields.Selection(
        selection=[
            ('after_other', 'After Other Promotions'),
            ('before_other', 'Before Other Promotions'),
        ],
        string='Promotion Order', default='after_other', required=True)
    min_amount = fields.Monetary(string='Minimum Basis', currency_field='currency_id', default=0.0)
    max_discount = fields.Monetary(string='Maximum Discount', currency_field='currency_id', default=0.0)
    bank_share_percent = fields.Float(string='Bank Share %', default=100.0)
    discount_product_id = fields.Many2one(
        'product.product', string='Discount Product', check_company=True,
        default=lambda self: self.env.ref(
            'pos_bank_card_program.product_bank_card_discount', raise_if_not_found=False))
    excluded_product_ids = fields.Many2many('product.product', string='Excluded Products')
    excluded_categ_ids = fields.Many2many('product.category', string='Excluded Categories')
    quota_type = fields.Selection(
        selection=[
            ('none', 'No Quota'),
            ('count', 'Number of Transactions'),
            ('amount', 'Discount Amount'),
        ],
        string='Quota Type', default='none', required=True)
    quota_period = fields.Selection(
        selection=[
            ('day', 'Per Day'),
            ('month', 'Per Month'),
            ('program', 'Whole Programme Period'),
        ],
        string='Quota Period', default='day', required=True)
    quota_limit_count = fields.Integer(string='Quota (Transactions)', default=0)
    quota_limit_amount = fields.Monetary(string='Quota (Amount)', currency_field='currency_id', default=0.0)
    card_daily_limit = fields.Integer(string='Uses per Card per Day', default=1)
    offline_allowed = fields.Boolean(string='Allow Offline', default=False)
    offline_allowance = fields.Integer(string='Offline Allowance per Outlet', default=0)
    refund_returns_quota = fields.Boolean(string='Refund Returns Quota', default=False)
    require_approval_code = fields.Boolean(string='Require Approval Code', default=True)
    discount_account_id = fields.Many2one(
        'account.account', string='Discount Account', check_company=True)
    receivable_account_id = fields.Many2one(
        'account.account', string='Bank Claim Receivable Account', check_company=True)
    journal_id = fields.Many2one('account.journal', string='Reclassification Journal', check_company=True)
    claim_period = fields.Selection(
        selection=[('month', 'Monthly')], string='Claim Period', default='month', required=True)
    claim_column_ids = fields.One2many(
        'pos.bank.card.claim.column', 'program_id', string='Claim Columns',
        default=lambda self: self._default_claim_columns())
    validity_label = fields.Char(string='Validity', compute='_compute_validity_label')
    usage_count = fields.Integer(string='Confirmed Usage', compute='_compute_usage_count')
    claim_count = fields.Integer(string='Claims', compute='_compute_claim_count')
    quota_left_label = fields.Char(string='Quota Left', compute='_compute_quota_left_label')

    @api.depends('date_from', 'date_to')
    def _compute_validity_label(self):
        for program in self:
            start = program.date_from and format_date(self.env, program.date_from)
            end = program.date_to and format_date(self.env, program.date_to)
            if start and end:
                program.validity_label = '%s - %s' % (start, end)
            elif start:
                program.validity_label = self.env._('From %s', start)
            elif end:
                program.validity_label = self.env._('Until %s', end)
            else:
                program.validity_label = self.env._('No limit')

    def _compute_usage_count(self):
        counts = dict(self.env['pos.bank.card.usage']._read_group(
            [('program_id', 'in', self.ids), ('state', '=', 'confirmed')],
            ['program_id'], ['__count']))
        for program in self:
            program.usage_count = counts.get(program, 0)

    def _program_claims(self):
        self.ensure_one()
        return self.env['pos.bank.card.usage'].search(
            [('program_id', '=', self.id), ('claim_id', '!=', False)]).claim_id

    def _compute_claim_count(self):
        claims = self.env['pos.bank.card.claim']
        readable = claims.has_access('read')
        for program in self:
            program.claim_count = len(program._program_claims()) if readable and program.id else 0

    def action_open_claims(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('pos_bank_card_program.action_pos_bank_card_claim')
        action['domain'] = [('id', 'in', self._program_claims().ids)]
        return action

    @api.depends('quota_type', 'quota_limit_count', 'quota_limit_amount', 'quota_period')
    def _compute_quota_left_label(self):
        today = fields.Date.context_today(self)
        for program in self:
            if program.quota_type == 'none' or not program.id:
                program.quota_left_label = self.env._('No quota')
                continue
            remaining = program._quota_remaining(today)
            if program.quota_type == 'count':
                program.quota_left_label = self.env._(
                    '%(left)s of %(limit)s', left=remaining, limit=program.quota_limit_count)
            else:
                program.quota_left_label = self.env._(
                    '%(left)s of %(limit)s',
                    left=format_amount(self.env, remaining, program.currency_id),
                    limit=format_amount(self.env, program.quota_limit_amount, program.currency_id))

    def action_open_usages(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('pos_bank_card_program.action_pos_bank_card_usage')
        action['domain'] = [('program_id', '=', self.id)]
        action['context'] = {'search_default_confirmed': 1}
        return action

    @api.model
    def _default_claim_columns(self):
        headers = [
            ('date', 'Date'),
            ('order', 'Order'),
            ('outlet', 'Outlet'),
            ('masked_card', 'Card'),
            ('approval_code', 'Approval Code'),
            ('basis_amount', 'Basis'),
            ('discount_amount', 'Discount'),
            ('bank_share_amount', 'Bank Share'),
        ]
        return [
            (0, 0, {'sequence': index * 10, 'code': code, 'header': header})
            for index, (code, header) in enumerate(headers)
        ]

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for program in self:
            if program.date_from and program.date_to and program.date_to < program.date_from:
                raise ValidationError(self.env._('The end date must not be before the start date.'))

    @api.constrains('discount_type', 'discount_percent', 'discount_fixed_amount', 'bank_share_percent')
    def _check_percents(self):
        for program in self:
            if program.discount_type == 'fixed':
                if program.discount_fixed_amount <= 0.0:
                    raise ValidationError(
                        self.env._('The fixed discount amount must be greater than zero.'))
            elif not 0.0 <= program.discount_percent <= 100.0:
                raise ValidationError(self.env._('The discount percent must be between 0 and 100.'))
            if not 0.0 <= program.bank_share_percent <= 100.0:
                raise ValidationError(self.env._('The bank share percent must be between 0 and 100.'))

    def action_activate(self):
        self.write({'state': 'active'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_set_draft(self):
        self.write({'state': 'draft'})

    def _is_valid_on(self, day):
        self.ensure_one()
        if self.state != 'active' or not self.active:
            return False
        if self.date_from and day < self.date_from:
            return False
        if self.date_to and day > self.date_to:
            return False
        return True

    def compute_discount(self, basis_amount):
        self.ensure_one()
        basis = basis_amount or 0.0
        if self.discount_type == 'fixed':
            discount = min(self.discount_fixed_amount, basis)
        else:
            discount = basis * self.discount_percent / 100.0
            if self.max_discount:
                discount = min(discount, self.max_discount)
        discount = max(discount, 0.0)
        if self.currency_id:
            discount = self.currency_id.round(discount)
        bank_share = discount * self.bank_share_percent / 100.0
        if self.currency_id:
            bank_share = self.currency_id.round(bank_share)
        return {
            'discount_amount': discount,
            'bank_share_amount': bank_share,
            'store_share_amount': discount - bank_share,
        }

    def _quota_bucket_dates(self, day):
        self.ensure_one()
        if self.quota_period == 'month':
            start = day.replace(day=1)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1) - datetime.timedelta(days=1)
            else:
                end = start.replace(month=start.month + 1) - datetime.timedelta(days=1)
            return start, end
        if self.quota_period == 'program':
            return self.date_from or day, self.date_to or day
        return day, day

    def _quota_for(self, day, create=False):
        self.ensure_one()
        start, end = self._quota_bucket_dates(day)
        Quota = self.env['pos.bank.card.quota'].sudo() if create else self.env['pos.bank.card.quota']
        quota = Quota.search([
            ('program_id', '=', self.id),
            ('date_from', '=', start),
            ('date_to', '=', end),
        ], limit=1)
        if not quota and create:
            try:
                with self.env.cr.savepoint():
                    quota = Quota.create({
                        'program_id': self.id,
                        'date_from': start,
                        'date_to': end,
                    })
            except psycopg2.IntegrityError:
                quota = Quota.search([
                    ('program_id', '=', self.id),
                    ('date_from', '=', start),
                    ('date_to', '=', end),
                ], limit=1)
                if not quota:
                    raise ConcurrencyError(
                        'quota bucket created concurrently; retry the reservation')
        return quota

    def _cardlimit_for(self, card_bin, card_last4, day, create=False):
        self.ensure_one()
        domain = [
            ('program_id', '=', self.id),
            ('card_bin', '=', card_bin),
            ('card_last4', '=', card_last4),
            ('date', '=', day),
        ]
        Cardlimit = self.env['pos.bank.card.cardlimit'].sudo() if create else self.env['pos.bank.card.cardlimit']
        limit = Cardlimit.search(domain, limit=1)
        if not limit and create:
            try:
                with self.env.cr.savepoint():
                    limit = Cardlimit.create({
                        'program_id': self.id,
                        'card_bin': card_bin,
                        'card_last4': card_last4,
                        'date': day,
                    })
            except psycopg2.IntegrityError:
                limit = Cardlimit.search(domain, limit=1)
                if not limit:
                    raise ConcurrencyError(
                        'daily card counter created concurrently; retry the reservation')
        return limit

    def _quota_remaining(self, day):
        self.ensure_one()
        if self.quota_type == 'none':
            return False
        quota = self._quota_for(day)
        if self.quota_type == 'count':
            used = quota.used_count if quota else 0
            return max(self.quota_limit_count - used, 0)
        used = quota.used_amount if quota else 0.0
        remaining = self.quota_limit_amount - used
        return remaining if remaining > 0 else 0.0

    def _lock_rows(self, quota, cardlimit):
        if quota:
            self.env.cr.execute(
                'SELECT id FROM pos_bank_card_quota WHERE id = %s FOR UPDATE', (quota.id,))
            quota.invalidate_recordset()
        if cardlimit:
            self.env.cr.execute(
                'SELECT id FROM pos_bank_card_cardlimit WHERE id = %s FOR UPDATE', (cardlimit.id,))
            cardlimit.invalidate_recordset()

    @api.model
    def _find_bin_range(self, card_bin, config_id=False, payment_method_id=False, program_id=False):
        if not card_bin or not str(card_bin).isdigit() or len(str(card_bin)) not in (6, 8):
            return self.env['pos.bank.card.bin.range']
        card_bin = str(card_bin)
        day = fields.Date.context_today(self)
        if config_id:
            company = self.env['pos.config'].browse(config_id).company_id
        else:
            company = self.env.company
        domain = [
            ('program_id.state', '=', 'active'),
            ('program_id.active', '=', True),
            ('program_id.company_id', '=', company.id),
            '|', ('program_id.date_from', '=', False), ('program_id.date_from', '<=', day),
            '|', ('program_id.date_to', '=', False), ('program_id.date_to', '>=', day),
        ]
        if program_id:
            domain.append(('program_id', '=', program_id))
        candidates = self.env['pos.bank.card.bin.range'].search(domain, order='id')
        low, high = bin_interval(card_bin, card_bin)
        matches = self.env['pos.bank.card.bin.range']
        for candidate in candidates:
            range_low, range_high = bin_interval(candidate.bin_from, candidate.bin_to)
            if low <= range_high and range_low <= high:
                program = candidate.program_id
                if config_id and program.config_ids and config_id not in program.config_ids.ids:
                    continue
                if payment_method_id and program.payment_method_ids and payment_method_id not in program.payment_method_ids.ids:
                    continue
                matches |= candidate
        if not matches:
            return matches
        exact = matches.filtered(lambda item: len(item.bin_from) == len(card_bin))
        return (exact or matches)[:1]

    @api.model
    def detect_card(self, card_bin, card_last4, basis_amount=0.0, config_id=False,
                    payment_method_id=False, online=True):
        result = {
            'eligible': False,
            'reason': False,
            'message': False,
            'program_id': False,
            'program_name': False,
            'bin_range_id': False,
            'network': False,
            'card_type': False,
            'card_tier': False,
            'basis_amount': basis_amount or 0.0,
            'discount_amount': 0.0,
            'bank_share_amount': 0.0,
            'store_share_amount': 0.0,
            'quota_type': False,
            'quota_remaining': False,
        }
        bin_range = self._find_bin_range(card_bin, config_id=config_id, payment_method_id=payment_method_id)
        if not bin_range:
            result['reason'] = 'bin_not_registered'
            result['message'] = self.env._('This card prefix is not registered in any active programme.')
            return result
        program = bin_range.program_id
        result.update({
            'program_id': program.id,
            'program_name': program.name,
            'bin_range_id': bin_range.id,
            'network': bin_range.network or False,
            'card_type': bin_range.card_type or False,
            'card_tier': bin_range.tier or False,
            'quota_type': program.quota_type,
        })
        if not bin_range.eligible:
            result['reason'] = 'tier_excluded'
            result['message'] = bin_range.ineligible_reason or self.env._(
                'This card tier is not included in the programme.')
            return result
        if not online and not program.offline_allowed:
            result['reason'] = 'offline'
            result['message'] = self.env._(
                'The point of sale is offline, so the shared quota cannot be checked.')
            return result
        if program.min_amount and (basis_amount or 0.0) < program.min_amount:
            result['reason'] = 'below_minimum'
            result['message'] = self.env._(
                'The purchase is below the programme minimum of %s.', program.min_amount)
            return result
        day = fields.Date.context_today(self)
        if program.card_daily_limit:
            cardlimit = program._cardlimit_for(str(card_bin), str(card_last4 or ''), day)
            if cardlimit and cardlimit.used_count >= program.card_daily_limit:
                result['reason'] = 'card_used_today'
                result['message'] = self.env._('This card has already been used today.')
                return result
        amounts = program.compute_discount(basis_amount or 0.0)
        remaining = program._quota_remaining(day)
        result['quota_remaining'] = remaining
        if program.quota_type == 'count' and remaining is not False and remaining < 1:
            result['reason'] = 'quota_exhausted'
            result['message'] = self.env._('The programme quota has run out.')
            return result
        if program.quota_type == 'amount' and remaining is not False and remaining < amounts['discount_amount']:
            result['reason'] = 'quota_exhausted'
            result['message'] = self.env._('The programme quota has run out.')
            return result
        result.update(amounts)
        result['eligible'] = True
        result['message'] = self.env._('%s applies to this card.', program.name)
        return result

    @api.model
    def reserve_quota(self, program_id, card_bin, card_last4, basis_amount, approval_code=False,
                      config_id=False, payment_method_id=False, pos_order_id=False, payment_id=False):
        program = self.browse(program_id)
        if not program.exists():
            return {
                'reserved': False,
                'reason': 'bin_not_registered',
                'message': self.env._('This card prefix is not registered in any active programme.'),
            }
        day = fields.Date.context_today(self)
        bin_range = self._find_bin_range(
            card_bin, config_id=config_id, payment_method_id=payment_method_id,
            program_id=program.id)
        if not bin_range or bin_range.program_id != program:
            return {
                'reserved': False,
                'reason': 'bin_not_registered',
                'message': self.env._('This card prefix is not registered in any active programme.'),
            }
        if not bin_range.eligible:
            return {
                'reserved': False,
                'reason': 'tier_excluded',
                'message': bin_range.ineligible_reason or self.env._(
                    'This card tier is not included in the programme.'),
            }
        if program.min_amount and (basis_amount or 0.0) < program.min_amount:
            return {
                'reserved': False,
                'reason': 'below_minimum',
                'message': self.env._(
                    'The purchase is below the programme minimum of %s.', program.min_amount),
            }
        amounts = program.compute_discount(basis_amount or 0.0)
        quota = program._quota_for(day, create=True) if program.quota_type != 'none' else self.env['pos.bank.card.quota']
        if program.quota_type != 'none' and not quota:
            raise ConcurrencyError(
                'quota bucket created concurrently; retry the reservation')
        cardlimit = self.env['pos.bank.card.cardlimit']
        if program.card_daily_limit:
            cardlimit = program._cardlimit_for(str(card_bin), str(card_last4 or ''), day, create=True)
            if not cardlimit:
                raise ConcurrencyError(
                    'daily card counter created concurrently; retry the reservation')
        program._lock_rows(quota, cardlimit)
        if cardlimit and cardlimit.used_count >= program.card_daily_limit:
            return {
                'reserved': False,
                'reason': 'card_used_today',
                'message': self.env._('This card has already been used today.'),
            }
        if quota and program.quota_type == 'count' and quota.used_count >= program.quota_limit_count:
            return {
                'reserved': False,
                'reason': 'quota_exhausted',
                'message': self.env._('The programme quota has run out.'),
            }
        if quota and program.quota_type == 'amount' and quota.used_amount + amounts['discount_amount'] > program.quota_limit_amount:
            return {
                'reserved': False,
                'reason': 'quota_exhausted',
                'message': self.env._('The programme quota has run out.'),
            }
        usage = self.env['pos.bank.card.usage'].sudo().create({
            'program_id': program.id,
            'bin_range_id': bin_range.id,
            'pos_order_id': pos_order_id or False,
            'payment_id': payment_id or False,
            'config_id': config_id or False,
            'quota_id': quota.id if quota else False,
            'cardlimit_id': cardlimit.id if cardlimit else False,
            'card_bin': str(card_bin),
            'card_last4': str(card_last4 or ''),
            'approval_code': approval_code or False,
            'card_brand': bin_range.network or False,
            'card_type': bin_range.card_type or False,
            'card_tier': bin_range.tier or False,
            'basis_amount': basis_amount or 0.0,
            'discount_amount': amounts['discount_amount'],
            'bank_share_amount': amounts['bank_share_amount'],
            'store_share_amount': amounts['store_share_amount'],
            'state': 'reserved',
        })
        if quota:
            quota.write({
                'used_count': quota.used_count + 1,
                'used_amount': quota.used_amount + amounts['discount_amount'],
            })
        if cardlimit:
            cardlimit.write({'used_count': cardlimit.used_count + 1})
        return {
            'reserved': True,
            'reason': False,
            'usage_id': usage.id,
            'quota_id': quota.id if quota else False,
            'discount_amount': amounts['discount_amount'],
            'bank_share_amount': amounts['bank_share_amount'],
            'store_share_amount': amounts['store_share_amount'],
            'quota_remaining': program._quota_remaining(day),
        }

    @api.model
    def release_quota(self, usage_id, config_id=False):
        usage = self.env['pos.bank.card.usage'].browse(usage_id)
        if not usage.exists():
            return {'released': False, 'reason': 'not_found'}
        if usage.config_id and usage.config_id.id != config_id:
            return {'released': False, 'reason': 'wrong_outlet'}
        return usage.release_reservation()

    @api.model
    def confirm_quota(self, usage_id, config_id=False):
        usage = self.env['pos.bank.card.usage'].browse(usage_id)
        if not usage.exists():
            return {'confirmed': False, 'reason': 'not_found'}
        if usage.config_id and usage.config_id.id != config_id:
            return {'confirmed': False, 'reason': 'wrong_outlet'}
        return usage.confirm_reservation()

    @api.model
    def verify_manager_pin(self, pin):
        from .res_users import check_manager_pin
        if not pin:
            return {'verified': False, 'reason': 'wrong'}
        caller = self.env.user.sudo()
        now = fields.Datetime.now()
        if caller.bank_card_pin_locked_until and caller.bank_card_pin_locked_until > now:
            return {'verified': False, 'reason': 'locked'}
        manager_group = self.env.ref(
            'pos_bank_card_program.group_bank_card_manager', raise_if_not_found=False)
        pos_manager_group = self.env.ref(
            'point_of_sale.group_pos_manager', raise_if_not_found=False)
        groups = (manager_group | pos_manager_group) if manager_group or pos_manager_group else self.env['res.groups']
        if not groups:
            return {'verified': False, 'reason': 'wrong'}
        users = self.env['res.users'].sudo().search([
            ('bank_card_manager_pin', '!=', False),
            ('all_group_ids', 'in', groups.ids),
            ('active', '=', True),
            ('company_ids', 'in', [self.env.company.id]),
        ])
        verified = any(
            check_manager_pin(str(pin), user.bank_card_manager_pin) for user in users)
        if verified:
            caller.write({'bank_card_pin_attempts': 0, 'bank_card_pin_locked_until': False})
            return {'verified': True, 'reason': False}
        attempts = caller.bank_card_pin_attempts + 1
        values = {'bank_card_pin_attempts': attempts}
        if attempts >= 5:
            values['bank_card_pin_locked_until'] = now + datetime.timedelta(minutes=5)
            values['bank_card_pin_attempts'] = 0
        caller.write(values)
        if values.get('bank_card_pin_locked_until'):
            return {'verified': False, 'reason': 'locked'}
        return {'verified': False, 'reason': 'wrong'}

    @api.model
    def _load_pos_data_domain(self, data, config):
        day = fields.Date.context_today(self)
        return [
            ('state', '=', 'active'),
            ('active', '=', True),
            ('company_id', '=', config.company_id.id),
            '|', ('date_from', '=', False), ('date_from', '<=', day),
            '|', ('date_to', '=', False), ('date_to', '>=', day),
            '|', ('config_ids', '=', False), ('config_ids', 'in', [config.id]),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'name', 'bank_id', 'mode', 'date_from', 'date_to',
            'discount_type', 'discount_percent', 'discount_fixed_amount',
            'discount_basis', 'promo_order', 'min_amount',
            'max_discount', 'bank_share_percent', 'discount_product_id',
            'excluded_product_ids', 'excluded_categ_ids', 'quota_type',
            'card_daily_limit', 'offline_allowed', 'offline_allowance',
            'require_approval_code', 'config_ids', 'payment_method_ids', 'bin_range_ids',
        ]
