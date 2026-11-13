from freezegun import freeze_time

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import XenditQrisCase, capture_payload


@tagged('post_install', '-at_install')
class TestStatementMemo(XenditQrisCase):

    def paid_qris(self, payment_uuid, amount=25000.0, reference='2610-1-0001'):
        request, _data, _fake = self.create_qris(amount=amount, payment_uuid=payment_uuid, order_reference=reference)
        self.post_webhook(capture_payload(request, payment_id='py-%s' % payment_uuid))
        request.invalidate_recordset()
        self.assertEqual(request.state, 'paid')
        return request

    def close(self):
        session = self.main_pos_config.current_session_id
        session.post_closing_cash_details(0)
        result = session.close_session_from_ui()
        self.assertTrue(result.get('successful'), result)
        return self.env['account.payment'].search([('pos_session_id', '=', session.id)])

    def test_order_paid_with_qris_carries_the_xendit_reference(self):
        request = self.paid_qris('line-a')
        order = self.sync(self.order_data('line-a', partner=self.customer))
        self.assertEqual(order.payment_ids.transaction_id, request.reference_id)
        self.assertEqual(request.pos_payment_id, order.payment_ids)

    def test_order_without_a_paid_qris_is_refused(self):
        self.create_qris(payment_uuid='line-unpaid')
        with self.assertRaisesRegex(ValidationError, 'no matching QRIS'):
            self.sync(self.order_data('line-unpaid', partner=self.customer))

    def test_draft_order_with_unpaid_qris_line_syncs_and_is_paid_once_xendit_pays(self):
        base = self.order_data('line-draft', partner=self.customer, reference='2610-1-0130')

        def payload(state, payment_status, order=None):
            line = dict(base['lines'][0][2], uuid='line-draft-product')
            payment = dict(base['payment_ids'][0][2], payment_status=payment_status)
            if order:
                return dict(base, state=state, access_token=order.access_token,
                            lines=[[1, order.lines.id, line]], payment_ids=[[1, order.payment_ids.id, payment]])
            return dict(base, state=state, lines=[[0, 0, line]], payment_ids=[[0, 0, payment]])

        order = self.sync(payload('draft', 'retry'))
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.payment_ids.transaction_id)
        with self.assertRaisesRegex(ValidationError, 'no matching QRIS'), self.env.cr.savepoint():
            self.sync(payload('paid', 'done', order))
        request = self.paid_qris('line-draft', reference='2610-1-0130')
        order = self.sync(payload('paid', 'done', order))
        self.assertEqual(order.state, 'paid')
        self.assertEqual(len(order.payment_ids), 1)
        self.assertEqual(order.payment_ids.transaction_id, request.reference_id)
        self.assertEqual(request.pos_payment_id, order.payment_ids)

    def test_one_paid_qris_cannot_pay_two_orders(self):
        self.paid_qris('line-twice')
        self.sync(self.order_data('line-twice', partner=self.customer, reference='2610-1-0002'))
        second = self.order_data('line-twice', partner=self.customer, reference='2610-1-0003')
        second['payment_ids'][0][2]['uuid'] = 'line-twice-copy'
        with self.assertRaises(ValidationError):
            self.sync(second)

    def test_memo_follows_the_agreed_format_with_the_reference_after_dash(self):
        self.pos_user.tz = 'Asia/Makassar'
        request = self.paid_qris('line-memo', reference='2610-1-0122')
        self.sync(self.order_data('line-memo', partner=self.customer, reference='2610-1-0122',
                                  date_order='2026-10-21 02:51:00'))
        payment = self.close().filtered(lambda p: p.pos_payment_method_id == self.qris)
        self.assertEqual(payment.memo, 'QRIS Xendit khusnul ari 21 October 2610-1-0122 10:51 - %s' % request.reference_id)
        self.assertEqual(payment.move_id.ref, payment.memo)

    def test_memo_month_follows_the_user_language(self):
        self.env['res.lang']._activate_lang('id_ID')
        self.pos_user.tz = 'Asia/Jakarta'
        self.paid_qris('line-id', reference='2610-1-0123')
        self.sync(self.order_data('line-id', partner=self.customer, reference='2610-1-0123',
                                  date_order='2026-10-21 02:51:00'))
        session = self.main_pos_config.current_session_id.with_context(lang='id_ID')
        session.post_closing_cash_details(0)
        session.close_session_from_ui()
        payment = self.env['account.payment'].search([('pos_session_id', '=', session.id), ('pos_payment_method_id', '=', self.qris.id)])
        self.assertIn('21 Oktober 2610-1-0123 09:51', payment.memo)

    def test_order_without_customer_gets_the_default_customer(self):
        request = self.paid_qris('line-walkin', reference='2610-1-0124')
        order = self.sync(self.order_data('line-walkin', reference='2610-1-0124'))
        self.assertEqual(order.partner_id, self.walk_in)
        payment = self.close().filtered(lambda p: p.pos_payment_method_id == self.qris)
        self.assertTrue(payment.memo.startswith('QRIS Xendit Walk-in Customer '))
        self.assertTrue(payment.memo.endswith(' - %s' % request.reference_id))

    def test_unticked_method_keeps_the_odoo_memo(self):
        self.qris.create_statement_memo = False
        self.paid_qris('line-plain', reference='2610-1-0125')
        self.sync(self.order_data('line-plain', partner=self.customer, reference='2610-1-0125'))
        payment = self.close().filtered(lambda p: p.pos_payment_method_id == self.qris)
        self.assertIn('POS payment of khusnul ari', payment.memo)

    def test_memo_on_a_plain_method_has_no_dash_part(self):
        transfer = self.env['pos.payment.method'].create({
            'name': 'Transfer', 'journal_id': self.bank_journal.id, 'split_transactions': True,
            'create_statement_memo': True})
        self.main_pos_config.payment_method_ids = [(4, transfer.id)]
        self.pos_user.tz = 'Asia/Makassar'
        order_data = self.order_data('line-transfer', partner=self.customer, reference='2610-1-0126',
                                     date_order='2026-10-21 02:51:00')
        order_data['payment_ids'][0][2]['payment_method_id'] = transfer.id
        with freeze_time('2026-10-21 03:00:00'):
            self.sync(order_data)
        payment = self.close().filtered(lambda p: p.pos_payment_method_id == transfer)
        self.assertNotIn(' - ', payment.memo)
        self.assertEqual(payment.memo, 'Transfer khusnul ari 21 October 2610-1-0126 10:51')

    def test_statement_memo_needs_identify_customer(self):
        with self.assertRaisesRegex(ValidationError, 'Identify Customer'):
            self.env['pos.payment.method'].create({
                'name': 'Memo without split', 'journal_id': self.bank_journal.id, 'create_statement_memo': True})
