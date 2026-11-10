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
        program = self.bank_card_program_id
        if program and program.require_approval_code:
            for payment in self._bank_card_payments():
                if not payment.payment_method_authcode:
                    raise UserError(self.env._(
                        'The Approval Code field is required by programme %s on order %s.',
                        program.name, self.name))

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
        Usage = self.env['pos.bank.card.usage'].sudo()
        if result.get('reserved'):
            return Usage.browse(result['usage_id'])
        amounts = program.compute_discount(basis)
        return Usage.create({
            'program_id': program.id,
            'pos_order_id': self.id,
            'payment_id': payment.id,
            'config_id': self.config_id.id,
            'card_bin': payment.card_bin or '',
            'card_last4': payment.card_no or '',
            'approval_code': payment.payment_method_authcode or False,
            'card_brand': payment.card_brand or False,
            'card_type': payment.card_type or False,
            'card_tier': payment.card_tier or False,
            'basis_amount': basis,
            'discount_amount': amounts['discount_amount'],
            'bank_share_amount': amounts['bank_share_amount'],
            'store_share_amount': amounts['store_share_amount'],
            'state': 'confirmed',
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

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if not fields:
            return fields
        return fields + ['is_bank_card_discount']
