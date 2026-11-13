import pytz

from odoo import _, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import format_date

MEMO_DATE_FORMAT = 'd MMMM'
MEMO_TIME_FORMAT = '%H:%M'


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    def _bind_xendit_qris_requests(self):
        """Tie each positive Xendit QRIS payment to its paid request, refusing one Xendit has not paid."""
        payments = self.filtered(lambda p: p.payment_method_id.use_payment_terminal == 'xendit_qris'
                                 and p.currency_id.compare_amounts(p.amount, 0) > 0)
        if not payments:
            return
        requests = self.env['pos.xendit.qris.request'].sudo().search([
            ('pos_payment_uuid', 'in', payments.mapped('uuid')), ('state', '=', 'paid')])
        by_uuid = {request.pos_payment_uuid: request for request in requests}
        for payment in payments:
            request = by_uuid.get(payment.uuid)
            if (not request or request.payment_method_id != payment.payment_method_id
                    or request.pos_payment_id and request.pos_payment_id != payment
                    or request.currency_id.compare_amounts(request.amount, payment.amount)):
                raise ValidationError(_(
                    'The %(method)s payment of %(amount)s in %(order)s has no matching QRIS paid at Xendit.',
                    method=payment.payment_method_id.name, amount=payment.amount, order=payment.pos_order_id.name))
            request.pos_payment_id = payment
            payment.transaction_id = request.reference_id

    def _statement_memo(self):
        """Return the customer payment memo of this payment, or False when its method does not ask for one."""
        self.ensure_one()
        method = self.payment_method_id
        if not method.create_statement_memo:
            return False
        order = self.pos_order_id
        tz = pytz.timezone(order.session_id.user_id.tz or self.env.user.tz or 'UTC')
        moment = pytz.utc.localize(order.date_order).astimezone(tz)
        lang = self.env.lang or self.env.user.lang or 'en_US'
        parts = [
            method.name,
            order.partner_id.name,
            format_date(self.env, moment.date(), lang_code=lang, date_format=MEMO_DATE_FORMAT),
            order.pos_reference,
            moment.strftime(MEMO_TIME_FORMAT),
        ]
        memo = ' '.join(part for part in parts if part)
        if method.use_payment_terminal == 'xendit_qris' and self.transaction_id:
            memo = '%s - %s' % (memo, self.transaction_id)
        return memo
