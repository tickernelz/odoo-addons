import hmac
import json
import logging

from odoo import http
from odoo.http import Response, request

from ..models.pos_xendit_qris_request import webhook_shape_problem

_logger = logging.getLogger(__name__)


class XenditQrisWebhook(http.Controller):

    @http.route('/pos_xendit_qris/webhook', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def xendit_qris_webhook(self):
        token = request.httprequest.headers.get('x-callback-token') or ''
        methods = request.env['pos.payment.method'].sudo().with_context(active_test=False).search(
            [('use_payment_terminal', '=', 'xendit_qris')])
        trusted = [method for method in methods if method.xendit_qris_webhook_token
                   and hmac.compare_digest(method.xendit_qris_webhook_token.encode(), token.encode())]
        if not token or not trusted:
            _logger.warning('Xendit QRIS webhook refused: missing or unknown callback token')
            return Response(status=403)
        try:
            payload = json.loads(request.httprequest.get_data())
        except ValueError:
            return Response(status=400)
        data = payload.get('data') if isinstance(payload, dict) else None
        reference = data.get('reference_id') if isinstance(data, dict) else None
        if not isinstance(reference, str) or not reference:
            return Response(status=400)
        problem = webhook_shape_problem(payload.get('event'), data)
        if problem:
            _logger.warning('Xendit QRIS webhook for %s refused: %s', reference, problem)
            return Response(status=400)
        qris = request.env['pos.xendit.qris.request'].sudo().search([('reference_id', '=', reference)], limit=1)
        if not qris:
            _logger.info('Xendit QRIS webhook for unknown reference %s', reference)
            return Response(status=200)
        if qris.payment_method_id not in trusted:
            _logger.warning('Xendit QRIS webhook for %s signed with another method token', reference)
            return Response(status=403)
        outcome = qris._apply_webhook(payload.get('event'), data)
        _logger.info('Xendit QRIS webhook %s for %s: %s', payload.get('event'), reference, outcome)
        return Response(status=200)
