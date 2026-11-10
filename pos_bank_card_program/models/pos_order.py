import datetime

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError


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
    bank_card_usage_ref = fields.Char(string='Bank Card Usage Reference')
    bank_card_offline = fields.Boolean(string='Bank Card Offline Discount', default=False)

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + [
            'bank_card_program_id', 'bank_card_masked',
            'bank_card_reservation_state', 'bank_card_usage_id',
            'bank_card_usage_ref', 'bank_card_offline',
        ]

    def _bank_card_payments(self):
        return self.payment_ids.filtered(lambda payment: payment.bank_card_program_id)

    def _check_bank_card_validation(self):
        self.ensure_one()
        discount_lines = self.lines.filtered('is_bank_card_discount')
        if not discount_lines:
            return
        if self.bank_card_offline:
            return
        if not self.bank_card_usage_ref and not self.bank_card_usage_id:
            raise UserError(self.env._(
                'The bank card discount on order %s has no usage record. '
                'Remove the discount line or apply the programme again so the quota is reserved.',
                self.name))
        program = self.bank_card_program_id
        card_payments = self._bank_card_payments()
        if program:
            card_payments = card_payments.filtered(
                lambda payment: payment.bank_card_program_id == program)
        if not card_payments:
            raise UserError(self.env._(
                'The bank card discount on order %s is not paid with the programme card '
                'payment. Pay with the card or remove the discount line.',
                self.name))
        usage = self.bank_card_usage_id
        if not usage and self.bank_card_usage_ref and str(self.bank_card_usage_ref).isdigit():
            usage = self.env['pos.bank.card.usage'].sudo().browse(
                int(self.bank_card_usage_ref)).exists()
        if usage:
            if usage.state == 'cancelled':
                raise UserError(self.env._(
                    'The bank card usage record on order %s was cancelled. '
                    'Remove the discount line or apply the programme again so the quota is reserved.',
                    self.name))
            if usage.pos_order_id and usage.pos_order_id != self:
                raise UserError(self.env._(
                    'The bank card usage record on order %s already belongs to another order.',
                    self.name))
            if self.bank_card_program_id and usage.program_id != self.bank_card_program_id:
                raise UserError(self.env._(
                    'The bank card usage record on order %s belongs to a different programme.',
                    self.name))
            if usage.config_id and usage.config_id != self.config_id:
                raise UserError(self.env._(
                    'The bank card usage record on order %s was reserved at a different outlet.',
                    self.name))
            line_total = abs(sum(discount_lines.mapped('price_subtotal_incl')))
            if self.currency_id.compare_amounts(line_total, usage.discount_amount) != 0:
                raise UserError(self.env._(
                    'The bank card discount lines on order %s do not match the reserved '
                    'discount amount.', self.name))
        if program and program.require_approval_code:
            for payment in self._bank_card_payments():
                if not payment.payment_method_authcode:
                    raise UserError(self.env._(
                        'The Approval Code field is required by programme %s on order %s.',
                        program.name, self.name))

    def _offline_day_bounds(self):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        local = fields.Datetime.context_timestamp(self, self.date_order or fields.Datetime.now())
        start_local = tz.localize(datetime.datetime(local.year, local.month, local.day))
        start = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        return start, start + datetime.timedelta(days=1)

    def _offline_allowance_left(self, program):
        self.ensure_one()
        if not program or not program.offline_allowed:
            return False
        start, end = self._offline_day_bounds()
        used = self.env['pos.bank.card.usage'].sudo().search_count([
            ('program_id', '=', program.id),
            ('config_id', '=', self.config_id.id),
            ('offline', '=', True),
            ('needs_review', '=', False),
            ('state', '=', 'confirmed'),
            ('pos_order_id.date_order', '>=', fields.Datetime.to_string(start)),
            ('pos_order_id.date_order', '<', fields.Datetime.to_string(end)),
            ('pos_order_id', '!=', self.id),
        ])
        return used < (program.offline_allowance or 0)

    def _offline_basis(self, program, payment):
        if program.discount_basis == 'card_amount':
            return payment.amount + payment.bank_card_discount
        lines = self.lines.filtered(
            lambda line: not line.is_bank_card_discount
            and line.product_id not in program.excluded_product_ids
            and line.product_id.categ_id not in program.excluded_categ_ids)
        if program.discount_basis == 'total':
            return sum(lines.mapped('price_subtotal_incl'))
        return sum(lines.mapped('price_subtotal'))

    def _masked_card_parts(self):
        digits = ''.join(char for char in (self.bank_card_masked or '') if char.isdigit())
        card_bin = digits[:-4] if len(digits) > 4 else ''
        card_last4 = digits[-4:] if len(digits) >= 4 else digits
        return card_bin or '000000', card_last4

    def _create_review_usage(self, program, payment, basis):
        discount_lines = self.lines.filtered('is_bank_card_discount')
        discount = abs(sum(discount_lines.mapped('price_subtotal_incl')))
        masked_bin, masked_last4 = self._masked_card_parts()
        usage = self.env['pos.bank.card.usage'].sudo().create({
            'program_id': program.id,
            'pos_order_id': self.id,
            'payment_id': payment.id if payment else False,
            'config_id': self.config_id.id,
            'card_bin': (payment.card_bin if payment else '') or masked_bin,
            'card_last4': (payment.card_no if payment else '') or masked_last4,
            'approval_code': (payment.payment_method_authcode if payment else '') or False,
            'card_brand': (payment.card_brand if payment else '') or False,
            'card_type': (payment.card_type if payment else '') or False,
            'card_tier': (payment.card_tier if payment else '') or False,
            'basis_amount': basis,
            'discount_amount': discount,
            'bank_share_amount': 0.0,
            'store_share_amount': discount,
            'state': 'confirmed',
            'claim_state': 'cancelled',
            'offline': True,
            'needs_review': True,
        })
        self.write({
            'bank_card_usage_id': usage.id,
            'bank_card_reservation_state': 'confirmed',
        })
        return usage

    def _settle_offline_usage(self):
        self.ensure_one()
        payment = self._bank_card_payments()[:1]
        program = self.bank_card_program_id or (payment.bank_card_program_id if payment else False)
        if not program:
            self.write({'bank_card_reservation_state': 'released'})
            self.message_post(body=self.env._(
                'A bank card discount was applied offline without a programme card '
                'payment, so no bank claim was recorded.'))
            return
        if not payment:
            self._create_review_usage(program, payment, 0.0)
            return
        Usage = self.env['pos.bank.card.usage'].sudo()
        usage = self.bank_card_usage_id
        if not usage and self.bank_card_usage_ref and str(self.bank_card_usage_ref).isdigit():
            usage = Usage.browse(int(self.bank_card_usage_ref)).exists()
        basis = self._offline_basis(program, payment)
        line_total = abs(sum(
            self.lines.filtered('is_bank_card_discount').mapped('price_subtotal_incl')))
        expected = program.compute_discount(basis)
        amounts_match = self.currency_id.compare_amounts(
            line_total, expected['discount_amount']) == 0
        approval_ok = not program.require_approval_code or bool(payment.payment_method_authcode)
        if not usage and approval_ok and amounts_match and self._offline_allowance_left(program):
            result = self.env['pos.bank.card.program'].reserve_quota(
                program.id, payment.card_bin or '', payment.card_no or '', basis,
                approval_code=payment.payment_method_authcode or False,
                config_id=self.config_id.id,
                payment_method_id=payment.payment_method_id.id,
                pos_order_id=self.id, payment_id=payment.id)
            if result.get('reserved'):
                usage = Usage.browse(result['usage_id'])
        if not usage:
            self._create_review_usage(program, payment, basis)
            return
        usage.write({
            'pos_order_id': self.id,
            'payment_id': payment.id,
            'offline': True,
            'state': 'confirmed',
        })
        self.write({
            'bank_card_usage_id': usage.id,
            'bank_card_reservation_state': 'confirmed',
        })

    def _confirm_bank_card_usage(self):
        self.ensure_one()
        if not self.lines.filtered('is_bank_card_discount'):
            return
        if self.bank_card_offline:
            self._settle_offline_usage()
            return
        Usage = self.env['pos.bank.card.usage'].sudo()
        usage = self.bank_card_usage_id
        if not usage and self.bank_card_usage_ref and str(self.bank_card_usage_ref).isdigit():
            usage = Usage.browse(int(self.bank_card_usage_ref)).exists()
        if not usage:
            return
        payment = self._bank_card_payments()[:1]
        values = {'pos_order_id': self.id}
        if payment:
            values['payment_id'] = payment.id
            if payment.payment_method_authcode and not usage.approval_code:
                values['approval_code'] = payment.payment_method_authcode
        if usage.state == 'reserved':
            values['state'] = 'confirmed'
        usage.write(values)
        self.write({
            'bank_card_usage_id': usage.id,
            'bank_card_reservation_state': 'confirmed',
        })

    def _cancel_refunded_bank_card_usage(self):
        self.ensure_one()
        if not self.is_refund:
            return
        refunded = self.refunded_order_id
        if refunded and refunded.bank_card_usage_id:
            refunded.bank_card_usage_id.cancel_for_refund()

    def action_pos_order_paid(self):
        self._check_bank_card_validation()
        result = super().action_pos_order_paid()
        self._confirm_bank_card_usage()
        self._cancel_refunded_bank_card_usage()
        return result


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    is_bank_card_discount = fields.Boolean(string='Bank Card Discount Line', default=False)

    def _bank_card_discount_program(self):
        if self.is_bank_card_discount:
            return self.order_id.bank_card_program_id
        refunded = self.refunded_orderline_id
        if refunded and refunded.is_bank_card_discount:
            return refunded.order_id.bank_card_program_id
        return self.env['pos.bank.card.program']

    def _prepare_base_line_for_taxes_computation(self):
        values = super()._prepare_base_line_for_taxes_computation()
        program = self._bank_card_discount_program()
        if program and program.discount_account_id:
            values['account_id'] = program.discount_account_id
        return values

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + ['is_bank_card_discount']
