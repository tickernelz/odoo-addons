import json
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import requests

from odoo import Command, fields
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

WEBHOOK_TOKEN = 'webhook-token-for-tests'
QR_STRING = '00020101021226570011ID.CO.QRIS.WWW0215ID20253456789010303UMI51440014ID.CO.QRIS.WWW'


class FakeResponse:

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class FakeXendit:
    """Stands in for requests.request and answers like Xendit Payments API v3."""

    def __init__(self):
        self.calls = []
        self.answers = []

    def queue(self, *answers):
        self.answers.extend(answers)

    def __call__(self, method, url, json=None, auth=None, timeout=None, headers=None):
        self.calls.append({'method': method, 'url': url, 'json': json, 'auth': auth, 'timeout': timeout, 'headers': headers})
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            if method == 'POST' and url.endswith('/v3/payment_requests'):
                return FakeResponse(201, created_answer(json['reference_id'], json['request_amount']))
            raise AssertionError('unexpected Xendit call %s %s' % (method, url))
        return answer


def created_answer(reference, amount, request_id=None):
    return {
        'payment_request_id': request_id or 'pr-%s' % reference,
        'reference_id': reference,
        'status': 'REQUIRES_ACTION',
        'request_amount': amount,
        'currency': 'IDR',
        'channel_code': 'QRIS',
        'actions': [{'type': 'PRESENT_TO_CUSTOMER', 'descriptor': 'QR_STRING', 'value': QR_STRING}],
    }


def status_answer(request, status, payment_id='py-0001', amount=None, currency='IDR'):
    return FakeResponse(200, {
        'payment_request_id': request.payment_request_id,
        'reference_id': request.reference_id,
        'status': status,
        'latest_payment_id': payment_id,
        'request_amount': request.amount if amount is None else amount,
        'currency': currency,
    })


def capture_payload(request, amount=None, payment_id='py-0001', currency='IDR', event='payment.capture', status='SUCCEEDED'):
    amount = request.amount if amount is None else amount
    return {
        'event': event,
        'business_id': 'biz-1',
        'created': '2026-11-13T03:00:00Z',
        'data': {
            'payment_id': payment_id,
            'reference_id': request.reference_id,
            'payment_request_id': request.payment_request_id,
            'type': 'PAY',
            'country': 'ID',
            'currency': currency,
            'request_amount': amount,
            'channel_code': 'QRIS',
            'status': status,
            'captures': [{'capture_id': 'cptr-1', 'capture_amount': amount}] if status == 'SUCCEEDED' else [],
        },
    }


class XenditQrisCase(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        idr = cls.env.ref('base.IDR')
        idr.active = True
        cls.env.company.currency_id = idr
        cls.main_pos_config.use_pricelist = False
        cls.main_pos_config.pricelist_id.currency_id = idr
        cls.walk_in = cls.env['res.partner'].create({'name': 'Walk-in Customer'})
        cls.customer = cls.env['res.partner'].create({'name': 'khusnul ari'})
        cls.qris = cls.env['pos.payment.method'].create({
            'name': 'QRIS Xendit',
            'journal_id': cls.bank_journal.id,
            'payment_method_type': 'terminal',
            'use_payment_terminal': 'xendit_qris',
            'xendit_qris_secret_key': 'xnd_development_secret',
            'xendit_qris_webhook_token': WEBHOOK_TOKEN,
            'split_transactions': True,
            'create_statement_memo': True,
            'statement_memo_partner_id': cls.walk_in.id,
        })
        cls.main_pos_config.payment_method_ids = [Command.link(cls.qris.id)]
        cls.qris_product = cls.env['product.product'].create({
            'name': 'QRIS Coffee',
            'type': 'service',
            'list_price': 25000.0,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
        })

    @contextmanager
    def fake_xendit(self, *answers):
        fake = FakeXendit()
        fake.queue(*answers)
        with patch('odoo.addons.pos_xendit_qris.models.xendit_client.requests.request', fake):
            yield fake

    def open_session(self):
        if not self.main_pos_config.current_session_id:
            self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        if session.state == 'opening_control':
            session.set_opening_control(0, '')
        return session

    def create_qris(self, amount=25000.0, payment_uuid=None, order_reference='2610-1-0001', user=None):
        session = self.open_session()
        payment_uuid = payment_uuid or str(uuid4())
        method = self.qris.with_user(user or self.pos_user)
        with self.fake_xendit() as fake:
            data = method.xendit_qris_create(session.id, 'order-%s' % payment_uuid, payment_uuid, amount, order_reference)
        request = self.env['pos.xendit.qris.request'].browse(data['id'])
        return request, data, fake

    def order_data(self, payment_uuid, amount=25000.0, partner=None, reference='2610-1-0001', date_order=None):
        session = self.open_session()
        return {
            'name': 'Order %s' % reference,
            'pos_reference': reference,
            'uuid': str(uuid4()),
            'session_id': session.id,
            'pricelist_id': self.main_pos_config.pricelist_id.id,
            'date_order': date_order or fields.Datetime.to_string(fields.Datetime.now()),
            'amount_paid': amount,
            'amount_return': 0.0,
            'amount_tax': 0.0,
            'amount_total': amount,
            'partner_id': partner.id if partner else False,
            'lines': [Command.create({
                'product_id': self.qris_product.id,
                'qty': amount / 25000.0,
                'price_unit': 25000.0,
                'price_subtotal': amount,
                'price_subtotal_incl': amount,
                'tax_ids': [],
            })],
            'payment_ids': [Command.create({
                'amount': amount,
                'payment_method_id': self.qris.id,
                'name': fields.Datetime.now(),
                'uuid': payment_uuid,
            })],
            'user_id': self.pos_user.id,
            'last_order_preparation_change': '{}',
        }

    def sync(self, *orders):
        result = self.env['pos.order'].sync_from_ui(list(orders))
        return self.env['pos.order'].browse([order['id'] for order in result['pos.order']])

    def post_webhook(self, payload, token=WEBHOOK_TOKEN, raw=None):
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['x-callback-token'] = token
        self.authenticate(None, None)
        return self.url_open('/pos_xendit_qris/webhook', data=raw if raw is not None else json.dumps(payload),
                             headers=headers, method='POST')


TIMEOUT_ERROR = requests.Timeout('read timed out')
