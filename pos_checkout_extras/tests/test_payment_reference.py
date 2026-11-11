import re

from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestPaymentReference(CheckoutExtrasCommon):

    def setUp(self):
        super().setUp()
        self.config.allow_payment_reference = True
        self.open_new_session()

    def split_order(self, reference):
        return self.sync(self.order_data(
            customer=self.customer,
            payments=[(self.cash_pm1, 40.0), (self.bank_pm1, 60.0)],
            payment_reference=reference,
        ))

    def test_every_payment_line_follows_the_order_reference(self):
        order = self.split_order('TRF-0001')
        self.assertEqual(len(order.payment_ids), 2)
        self.assertEqual(order.payment_ids.mapped('payment_reference'), ['TRF-0001', 'TRF-0001'])
        order.payment_reference = 'TRF-0002'
        self.assertEqual(order.payment_ids.mapped('payment_reference'), ['TRF-0002', 'TRF-0002'])
        self.env.flush_all()
        stored = self.env.execute_query(self.env['pos.payment']._search(
            [('pos_order_id', '=', order.id), ('payment_reference', '=', 'TRF-0002')]).select())
        self.assertEqual(len(stored), 2)

    def test_reference_longer_than_64_characters_is_cut_to_64(self):
        order = self.split_order('R' * 65)
        self.assertEqual(order.payment_reference, 'R' * 64)
        self.assertEqual(set(order.payment_ids.mapped('payment_reference')), {'R' * 64})

    def test_reference_is_optional(self):
        order = self.split_order(False)
        self.assertFalse(order.payment_reference)
        self.assertFalse(any(order.payment_ids.mapped('payment_reference')))

    def test_payments_are_searchable_and_grouped_by_reference(self):
        first = self.split_order('EDC-11')
        self.split_order('EDC-22')
        self.split_order(False)
        Payment = self.env['pos.payment']
        self.assertEqual(Payment.search([('payment_reference', '=', 'EDC-11')]), first.payment_ids)
        groups = dict(Payment._read_group(
            [('session_id', '=', self.pos_session.id), ('payment_reference', '!=', False)],
            ['payment_reference'], ['__count']))
        self.assertEqual(groups, {'EDC-11': 2, 'EDC-22': 2})
        missing = self.env['pos.order'].search([('session_id', '=', self.pos_session.id), ('payment_reference', '=', False)])
        self.assertEqual(len(missing), 1)

    def test_order_filters_find_missing_customers_and_references(self):
        with_reference = self.split_order('VA-1')
        without_customer = self.sync(self.order_data())
        draft = self.sync(self.order_data(state='draft'))
        arch = self.env['pos.order'].get_views([(False, 'search')])['views']['search']['arch']
        filters = {
            name: self.env['pos.order'].search(
                [('session_id', '=', self.pos_session.id)] + safe_eval(domain))
            for name, domain in re.findall(r'<filter[^>]*name="(missing_customer|with_payment_reference)"[^>]*domain="([^"]*)"', arch)
        }
        self.assertEqual(filters['missing_customer'], without_customer)
        self.assertNotIn(draft, filters['missing_customer'])
        self.assertEqual(filters['with_payment_reference'], with_reference)

    def test_pos_analysis_carries_the_reference(self):
        order = self.split_order('QRIS-7')
        self.env.flush_all()
        rows = self.env['report.pos.order'].search([('order_id', '=', order.id)])
        self.assertTrue(rows)
        self.assertEqual(set(rows.mapped('payment_reference')), {'QRIS-7'})
