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
        if not self.bank_card_usage_ref and not self.bank_card_offline and not self.bank_card_usage_id:
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
        if self.bank_card_offline:
            self._check_offline_allowed(program)
        usage = self.bank_card_usage_id
        if not usage and self.bank_card_usage_ref and str(self.bank_card_usage_ref).isdigit():
            usage = self.env['pos.bank.card.usage'].sudo().browse(
                int(self.bank_card_usage_ref)).exists()
        if usage and not self.bank_card_offline:
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

    def _check_offline_allowed(self, program):
        self.ensure_one()
        if not program or not program.offline_allowed:
            raise UserError(self.env._(
                'The bank card discount on order %s was applied offline, but programme %s '
                'does not allow offline discounts.',
                self.name, program.name if program else ''))
        day = fields.Date.context_today(self)
        start = fields.Datetime.to_datetime(day)
        used = self.env['pos.bank.card.usage'].sudo().search_count([
            ('program_id', '=', program.id),
            ('config_id', '=', self.config_id.id),
            ('offline', '=', True),
            ('state', '=', 'confirmed'),
            ('date', '>=', fields.Datetime.to_string(start)),
            ('pos_order_id', '!=', self.id),
        ])
        if used >= (program.offline_allowance or 0):
            raise UserError(self.env._(
                'The offline discount allowance of programme %s for this outlet is used up '
                'on order %s.', program.name, self.name))

    def _confirm_bank_card_usage(self):
        self.ensure_one()
        if not self.lines.filtered('is_bank_card_discount'):
            return
        Usage = self.env['pos.bank.card.usage'].sudo()
        usage = self.bank_card_usage_id
        if not usage and self.bank_card_usage_ref and str(self.bank_card_usage_ref).isdigit():
            usage = Usage.browse(int(self.bank_card_usage_ref)).exists()
        payment = self._bank_card_payments()[:1]
        if not usage and self.bank_card_offline and payment:
            usage = self._create_offline_usage(payment)
        if not usage:
            return
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

    def _create_offline_usage(self, payment):
        program = self.bank_card_program_id or payment.bank_card_program_id
        if not program:
            return self.env['pos.bank.card.usage']
        if program.discount_basis == 'card_amount':
            basis = payment.amount + payment.bank_card_discount
        elif program.discount_basis == 'total':
            basis = sum(
                line.price_subtotal_incl for line in self.lines
                if not line.is_bank_card_discount
                and line.product_id not in program.excluded_product_ids
                and line.product_id.categ_id not in program.excluded_categ_ids)
        else:
            basis = sum(
                line.price_subtotal for line in self.lines
                if not line.is_bank_card_discount
                and line.product_id not in program.excluded_product_ids
                and line.product_id.categ_id not in program.excluded_categ_ids)
        result = self.env['pos.bank.card.program'].reserve_quota(
            program.id, payment.card_bin or '', payment.card_no or '', basis,
            approval_code=payment.payment_method_authcode or False,
            config_id=self.config_id.id,
            payment_method_id=payment.payment_method_id.id,
            pos_order_id=self.id, payment_id=payment.id)
        if not result.get('reserved'):
            raise UserError(self.env._(
                'The offline bank card discount on order %s cannot be recorded: %s',
                self.name, result.get('message') or result.get('reason') or ''))
        usage = self.env['pos.bank.card.usage'].sudo().browse(result['usage_id'])
        usage.write({'offline': True})
        return usage

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

    def _prepare_base_line_for_taxes_computation(self):
        values = super()._prepare_base_line_for_taxes_computation()
        if self.is_bank_card_discount:
            program = self.order_id.bank_card_program_id
            if program and program.discount_account_id:
                values['account_id'] = program.discount_account_id
        return values

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + ['is_bank_card_discount']
