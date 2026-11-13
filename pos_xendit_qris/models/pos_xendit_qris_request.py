import base64
import io
import logging
import re
from datetime import timedelta

import qrcode

from odoo import _, api, fields, models

from .xendit_client import XenditError

_logger = logging.getLogger(__name__)

CLOSED_STATES = {'EXPIRED': 'expired', 'CANCELED': 'cancelled', 'FAILED': 'failed'}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def webhook_shape_problem(event, data):
    """Return why a Xendit webhook body cannot be applied, or None when its fields have the expected types."""
    if not isinstance(data.get('payment_request_id'), str) or not data['payment_request_id']:
        return 'payment_request_id missing'
    if event != 'payment.capture':
        return None
    if not isinstance(data.get('payment_id'), str) or not data['payment_id']:
        return 'payment_id missing'
    if not isinstance(data.get('currency'), str) or not isinstance(data.get('status'), str):
        return 'currency or status missing'
    captures = data.get('captures')
    if captures:
        if not isinstance(captures, list) or not all(
                isinstance(capture, dict) and _is_number(capture.get('capture_amount')) for capture in captures):
            return 'captures malformed'
    elif not _is_number(data.get('request_amount')):
        return 'amount missing'
    return None


class PosXenditQrisRequest(models.Model):
    _name = 'pos.xendit.qris.request'
    _description = 'Xendit QRIS Request'
    _order = 'id desc'
    _rec_name = 'reference_id'

    reference_id = fields.Char(string='Reference', readonly=True, index=True, copy=False)
    payment_method_id = fields.Many2one('pos.payment.method', required=True, readonly=True, ondelete='restrict')
    session_id = fields.Many2one('pos.session', required=True, readonly=True, index=True, ondelete='restrict')
    config_id = fields.Many2one(related='session_id.config_id', store=True, string='Point of Sale')
    company_id = fields.Many2one(related='session_id.company_id', store=True)
    pos_order_uuid = fields.Char(string='Order UUID', readonly=True)
    pos_payment_uuid = fields.Char(string='Payment Line UUID', readonly=True, index=True)
    pos_payment_id = fields.Many2one('pos.payment', string='POS Payment', readonly=True, index='btree_not_null')
    order_reference = fields.Char(string='Order Reference', readonly=True)
    amount = fields.Monetary(readonly=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    payment_request_id = fields.Char(string='Xendit Payment Request', readonly=True, copy=False)
    payment_id = fields.Char(string='Xendit Payment', readonly=True, copy=False)
    qr_string = fields.Char(string='QRIS String', readonly=True, copy=False)
    expires_at = fields.Datetime(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    state = fields.Selection([
        ('pending', 'Waiting for payment'),
        ('paid', 'Paid'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ], default='pending', required=True, readonly=True, index=True)
    attention = fields.Char(readonly=True, help='Set when Xendit reported something the cashier could not settle, such as a payment after the QRIS was cancelled.')

    _reference_unique = models.Constraint('UNIQUE(reference_id)', 'A QRIS reference must be unique.')
    _payment_request_unique = models.Constraint('UNIQUE(payment_request_id)', 'A Xendit payment request is recorded once.')

    @api.model
    def _pending_for(self, payment_uuid, amount):
        now = fields.Datetime.now()
        for request in self.search([('pos_payment_uuid', '=', payment_uuid), ('state', 'in', ('pending', 'paid'))]):
            if request.currency_id.compare_amounts(request.amount, amount):
                continue
            if request.state == 'paid' or request.expires_at > now:
                return request
        return self.browse()

    def _open_at_xendit(self, client):
        self.ensure_one()
        prefix = re.sub(r'[^A-Za-z0-9-]', '', self.order_reference or '') or 'POS'
        self.reference_id = '%s-%s' % (prefix, self.id)
        expires_at = fields.Datetime.now().replace(microsecond=0) + timedelta(
            minutes=self.payment_method_id.xendit_qris_expiry_minutes)
        answer = client.create_qris(
            self.reference_id, int(self.amount), expires_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
            '%s %s' % (self.config_id.name, self.reference_id))
        qr_string = next((action.get('value') for action in answer.get('actions') or []
                          if action.get('descriptor') == 'QR_STRING'), None)
        if not qr_string or not answer.get('payment_request_id'):
            raise XenditError(_('the answer carries no QRIS'))
        self.write({
            'payment_request_id': answer['payment_request_id'],
            'qr_string': qr_string,
            'expires_at': expires_at,
        })

    def _pos_status(self):
        self.ensure_one()
        return {
            'id': self.id,
            'state': self.state,
            'reference': self.reference_id,
        }

    def _pos_data(self):
        self.ensure_one()
        image = b''
        if self.state == 'pending':
            buffer = io.BytesIO()
            qrcode.make(self.qr_string, box_size=8, border=2).save(buffer, format='PNG')
            image = buffer.getvalue()
        return dict(
            self._pos_status(),
            amount=self.amount,
            expires_at=fields.Datetime.to_string(self.expires_at),
            qr_image='data:image/png;base64,' + base64.b64encode(image).decode() if image else False,
        )

    def _lock(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM pos_xendit_qris_request WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def _notify_pos(self):
        for request in self:
            request.config_id._notify('XENDIT_QRIS', {'request_id': request.id, 'state': request.state})

    def _record_capture(self, payment_id, amount, currency):
        """Mark the request paid when Xendit's capture matches it, and say what happened."""
        self.ensure_one()
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = None
        if currency != self.currency_id.name or amount is None or self.currency_id.compare_amounts(amount, self.amount):
            self.attention = _('Xendit reported %(currency)s %(amount)s for %(expected)s.',
                               currency=currency, amount=amount, expected=self.amount)
            _logger.warning('Xendit capture for %s does not match: %s %s', self.reference_id, currency, amount)
            return 'amount_mismatch'
        if self.state == 'paid':
            if self.payment_id == payment_id:
                return 'duplicate'
            self.attention = _('Xendit reported a second payment %(payment)s.', payment=payment_id)
            return 'second_payment'
        if self.state != 'pending':
            self.attention = _('Paid at Xendit (%(payment)s) after the QRIS was %(state)s; settle it by hand.',
                               payment=payment_id, state=self.state)
            _logger.warning('Xendit payment %s arrived for %s request %s', payment_id, self.state, self.reference_id)
            return 'late_payment'
        self.write({'state': 'paid', 'payment_id': payment_id, 'paid_at': fields.Datetime.now()})
        self._notify_pos()
        return 'paid'

    def _record_closed(self, state):
        self.ensure_one()
        if self.state != 'pending':
            return 'ignored'
        self.state = state
        self._notify_pos()
        return state

    def _apply_xendit_answer(self, answer):
        self.ensure_one()
        status = answer.get('status')
        if status == 'SUCCEEDED':
            return self._record_capture(
                answer.get('latest_payment_id') or answer.get('payment_id'),
                answer.get('request_amount'), answer.get('currency'))
        if status in CLOSED_STATES:
            return self._record_closed(CLOSED_STATES[status])
        return 'pending'

    def _sync_from_xendit(self, client):
        self.ensure_one()
        answer = client.get(self.payment_request_id)
        self._lock()
        return self._apply_xendit_answer(answer)

    def _cancel_at_xendit(self, client):
        self.ensure_one()
        try:
            answer = client.cancel(self.payment_request_id)
        except XenditError:
            if self._sync_from_xendit(client) == 'pending':
                raise
            return self.state
        self._lock()
        if answer.get('status') == 'CANCELED':
            return self._record_closed('cancelled')
        return self._apply_xendit_answer(answer)

    def _apply_webhook(self, event, data):
        """Apply a verified Xendit webhook to this request."""
        self.ensure_one()
        self._lock()
        if self.payment_request_id != data.get('payment_request_id'):
            _logger.warning('Xendit webhook for %s names another payment request', self.reference_id)
            return 'request_mismatch'
        if event == 'payment.capture' and data.get('status') == 'SUCCEEDED':
            captures = data.get('captures') or []
            amount = sum(capture['capture_amount'] for capture in captures) if captures else data['request_amount']
            return self._record_capture(data.get('payment_id'), amount, data.get('currency'))
        if event == 'payment.failure':
            return self._record_closed('failed')
        if event == 'payment_request.expiry':
            return self._record_closed('expired')
        return 'ignored'
