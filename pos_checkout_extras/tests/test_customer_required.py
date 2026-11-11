from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestCustomerRequired(CheckoutExtrasCommon):

    def setUp(self):
        super().setUp()
        self.config.customer_required_mode = 'payment'
        self.open_new_session()

    def test_since_is_stamped_only_when_the_mode_leaves_off(self):
        config = self.env['pos.config'].create({'name': 'Counter Since'})
        self.assertFalse(config.customer_required_since)
        config.customer_required_mode = 'order'
        first = config.customer_required_since
        self.assertTrue(first)
        config.customer_required_since = first - timedelta(days=3)
        config.customer_required_mode = 'payment'
        self.assertEqual(config.customer_required_since, first - timedelta(days=3))
        config.customer_required_mode = 'off'
        config.customer_required_mode = 'order'
        self.assertGreaterEqual(config.customer_required_since, first)

    def test_new_counter_created_with_a_mode_gets_since(self):
        config = self.env['pos.config'].create({'name': 'Counter Born Required', 'customer_required_mode': 'order'})
        self.assertTrue(config.customer_required_since)

    def test_paid_order_without_customer_is_refused(self):
        with self.assertRaisesRegex(ValidationError, 'has no customer'), self.env.cr.savepoint():
            self.sync(self.order_data())
        self.env.invalidate_all()
        self.assertFalse(self.pos_session.order_ids)

    def test_paid_order_with_customer_is_accepted(self):
        order = self.sync(self.order_data(customer=self.customer))
        self.assertEqual(order.state, 'paid')

    def test_draft_order_without_customer_is_accepted(self):
        order = self.sync(self.order_data(state='draft'))
        self.assertEqual(order.state, 'draft')

    def test_refund_without_customer_is_accepted(self):
        original = self.sync(self.order_data(customer=self.customer))
        refund_data = self.create_ui_order_data(
            [{'product': self.product, 'quantity': -1, 'refunded_orderline_id': original.lines.id}],
            payments=[(self.cash_pm1, -100.0)],
            pos_order_ui_args={'is_refund': True},
        )
        refund = self.sync(refund_data)
        self.assertEqual(refund.state, 'paid')
        self.assertFalse(refund.partner_id)

    def test_order_dated_before_the_mode_was_turned_on_is_accepted(self):
        before = self.config.customer_required_since - timedelta(hours=1)
        order = self.sync(self.order_data(date_order=fields.Datetime.to_string(before)))
        self.assertEqual(order.state, 'paid')

    def test_counter_without_the_mode_accepts_orders_without_customer(self):
        self.pos_session.config_id.write({'customer_required_mode': 'off'})
        order = self.sync(self.order_data())
        self.assertEqual(order.state, 'paid')
