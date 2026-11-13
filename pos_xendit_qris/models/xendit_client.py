import logging

import requests

_logger = logging.getLogger(__name__)

XENDIT_URL = 'https://api.xendit.co'
API_VERSION = '2024-11-11'
TIMEOUT = (5, 15)


class XenditError(Exception):
    """Xendit refused the call or could not be reached."""


class XenditQrisClient:
    """Xendit Payments API v3 calls used by QRIS in the point of sale."""

    def __init__(self, secret_key):
        self._auth = (secret_key, '')

    def _call(self, method, path, payload=None):
        try:
            response = requests.request(
                method, XENDIT_URL + path, json=payload, auth=self._auth, timeout=TIMEOUT,
                headers={'api-version': API_VERSION, 'Content-Type': 'application/json'})
        except requests.RequestException as error:
            _logger.warning('Xendit %s %s failed: %s', method, path, error)
            raise XenditError(str(error) or type(error).__name__) from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            reason = body.get('message') or body.get('error_code') or 'HTTP %s' % response.status_code
            _logger.warning('Xendit %s %s answered %s: %s', method, path, response.status_code, reason)
            raise XenditError(reason)
        if not isinstance(body, dict):
            raise XenditError('unexpected answer')
        return body

    def create_qris(self, reference, amount, expires_at, description):
        return self._call('POST', '/v3/payment_requests', {
            'reference_id': reference,
            'type': 'PAY',
            'country': 'ID',
            'currency': 'IDR',
            'request_amount': amount,
            'capture_method': 'AUTOMATIC',
            'channel_code': 'QRIS',
            'channel_properties': {'expires_at': expires_at},
            'description': description,
        })

    def get(self, payment_request_id):
        return self._call('GET', '/v3/payment_requests/%s' % payment_request_id)

    def cancel(self, payment_request_id):
        return self._call('POST', '/v3/payment_requests/%s/cancel' % payment_request_id)
