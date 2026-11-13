from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .xendit_client import XenditError, XenditQrisClient

QRIS_MAX_AMOUNT = 10_000_000


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    create_statement_memo = fields.Boolean(
        string='Create Statement Memo',
        help='Write the memo of each customer payment as: method, customer, order date, order reference, '
             'order time, and for Xendit QRIS the Xendit reference after " - ". Needs Identify Customer.')
    statement_memo_partner_id = fields.Many2one(
        'res.partner', string='Default Customer',
        help='Set on the order when the cashier has not chosen a customer, so Identify Customer does not block the sale.')
    xendit_qris_secret_key = fields.Char(
        string='Xendit Secret Key', copy=False, groups='base.group_system')
    xendit_qris_webhook_token = fields.Char(
        string='Xendit Webhook Verification Token', copy=False, groups='base.group_system')
    xendit_qris_expiry_minutes = fields.Integer(string='QR Validity (minutes)', default=15)
    xendit_qris_webhook_url = fields.Char(string='Xendit Webhook URL', compute='_compute_xendit_qris_webhook_url')

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('xendit_qris', 'Xendit QRIS')]

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ['create_statement_memo', 'statement_memo_partner_id']

    def _compute_xendit_qris_webhook_url(self):
        url = self.get_base_url().rstrip('/') + '/pos_xendit_qris/webhook'
        for method in self:
            method.xendit_qris_webhook_url = url if method.use_payment_terminal == 'xendit_qris' else False

    @api.constrains('create_statement_memo', 'split_transactions')
    def _check_statement_memo(self):
        for method in self:
            if method.create_statement_memo and not method.split_transactions:
                raise ValidationError(_(
                    'Create Statement Memo on %(method)s needs Identify Customer: '
                    'without it the session books one combined payment, not one per order.', method=method.name))

    @api.constrains('use_payment_terminal', 'xendit_qris_expiry_minutes')
    def _check_xendit_qris_expiry(self):
        for method in self.filtered(lambda m: m.use_payment_terminal == 'xendit_qris'):
            if not 1 <= method.xendit_qris_expiry_minutes <= 2880:
                raise ValidationError(_('QR validity must be between 1 and 2880 minutes.'))

    def _xendit_qris_client(self):
        self.ensure_one()
        key = self.sudo().xendit_qris_secret_key
        if not key:
            raise UserError(_('The Xendit secret key of %(method)s is not set.', method=self.name))
        return XenditQrisClient(key)

    def _xendit_qris_open_session(self, session_id):
        self.ensure_one()
        if self.use_payment_terminal != 'xendit_qris':
            raise UserError(_('%(method)s is not a Xendit QRIS payment method.', method=self.name))
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Only point of sale users can take QRIS payments.'))
        session = self.env['pos.session'].browse(int(session_id)).exists()
        if not session or session.state != 'opened' or self not in session.config_id.payment_method_ids:
            raise UserError(_('The point of sale session is not open for %(method)s.', method=self.name))
        session.check_access('read')
        return session

    def _xendit_qris_request_of(self, request_id):
        request = self.env['pos.xendit.qris.request'].sudo().browse(int(request_id)).exists()
        if not request or request.payment_method_id != self:
            raise UserError(_('This QRIS request does not exist.'))
        self._xendit_qris_open_session(request.session_id.id)
        return request

    def xendit_qris_create(self, session_id, order_uuid, payment_uuid, amount, order_reference):
        """Return the QRIS of a payment line, creating it at Xendit unless one is still valid."""
        session = self._xendit_qris_open_session(session_id)
        currency = session.currency_id
        if currency.name != 'IDR':
            raise UserError(_('QRIS only takes IDR; this session uses %(currency)s.', currency=currency.name))
        amount = float(amount or 0)
        if currency.compare_amounts(amount, round(amount)) or amount < 1:
            raise UserError(_('QRIS takes whole rupiah amounts of at least 1.'))
        if amount > QRIS_MAX_AMOUNT:
            raise UserError(_('QRIS takes at most %(limit)s per payment.', limit=f'{QRIS_MAX_AMOUNT:,}'))
        Request = self.env['pos.xendit.qris.request'].sudo()
        request = Request._pending_for(payment_uuid, amount)
        if request:
            if request.session_id != session:
                raise UserError(_('This payment line belongs to another session.'))
            return request._pos_data()
        request = Request.create({
            'payment_method_id': self.id,
            'session_id': session.id,
            'pos_order_uuid': order_uuid,
            'pos_payment_uuid': payment_uuid,
            'order_reference': order_reference,
            'amount': amount,
            'currency_id': currency.id,
        })
        try:
            request._open_at_xendit(self._xendit_qris_client())
        except XenditError as error:
            raise UserError(_('Xendit could not create the QRIS: %(reason)s', reason=error)) from error
        return request._pos_data()

    def xendit_qris_status(self, request_id, ask_xendit=False):
        """Return the state of a QRIS request, asking Xendit first when requested."""
        self.ensure_one()
        request = self._xendit_qris_request_of(request_id)
        if ask_xendit and request.state == 'pending':
            try:
                request._sync_from_xendit(self._xendit_qris_client())
            except XenditError as error:
                raise UserError(_('Xendit could not be reached: %(reason)s', reason=error)) from error
        return request._pos_status()

    def xendit_qris_cancel(self, request_id):
        """Cancel a pending QRIS at Xendit; a QRIS already paid stays paid."""
        self.ensure_one()
        request = self._xendit_qris_request_of(request_id)
        if request.state == 'pending':
            try:
                request._cancel_at_xendit(self._xendit_qris_client())
            except XenditError as error:
                raise UserError(_('Xendit could not cancel the QRIS: %(reason)s', reason=error)) from error
        return request._pos_status()
