from odoo.tests import tagged

from .common import MANAGER_PIN, ManagerValidationCommon


@tagged('post_install', '-at_install')
class TestFlags(ManagerValidationCommon):

    def test_unvalidated_discount_is_flagged_not_refused(self):
        self.guard('discount')
        session = self.open_session()
        order = self.sync(session, self.order_data(session, discount=10.0))
        self.assertEqual(order.state, 'paid')
        self.assertTrue(order.mv_flagged)
        self.assertEqual(order.mv_unvalidated, 'discount')
        self.assertEqual(order.mv_unvalidated_display, 'Discount')

    def test_validated_actions_are_not_flagged(self):
        self.guard('discount', 'payment')
        session = self.open_session()
        data = self.order_data(session, discount=10.0)
        for action in ('discount', 'payment'):
            self.verify(session, action, MANAGER_PIN, reference=data['uuid'])
        order = self.sync(session, data)
        self.assertFalse(order.mv_flagged)

    def test_refund_is_checked_against_the_original_order(self):
        self.guard('refund')
        session = self.open_session()
        original = self.sync(session, self.order_data(session, qty=2))
        refund = self.order_data(session, qty=-1, refunded_line=original.lines)
        refund['amount_paid'] = refund['amount_total']
        self.assertTrue(self.sync(session, refund).mv_flagged)
        second = self.order_data(session, qty=-1, refunded_line=original.lines)
        self.verify(session, 'refund', MANAGER_PIN, reference=original.uuid)
        self.assertFalse(self.sync(session, second).mv_flagged)

    def test_orders_of_the_manager_at_the_register_are_not_flagged(self):
        self.guard('discount')
        session = self.open_session()
        session.employee_id = self.manager
        self.verify(session, 'discount', MANAGER_PIN, reference='first', remember=True)
        order = self.sync(session, self.order_data(session, discount=10.0, employee=self.manager))
        self.assertFalse(order.mv_flagged)

    def test_prices_set_by_other_modules_are_not_flagged(self):
        self.guard('price', 'discount')
        session = self.open_session()
        order = self.sync(session, self.order_data(session, price=0.5))
        self.assertFalse(order.mv_flagged)

    def test_refund_lines_keep_the_original_discount_without_a_flag(self):
        session = self.open_session()
        original = self.sync(session, self.order_data(session, discount=10.0, qty=2))
        self.guard('discount')
        refund = self.order_data(session, discount=10.0, qty=-1, refunded_line=original.lines)
        self.assertFalse(self.sync(session, refund).mv_flagged)

    def test_nothing_is_flagged_when_validation_is_off(self):
        session = self.open_session()
        order = self.sync(session, self.order_data(session, discount=10.0, price=3.0))
        self.assertFalse(order.mv_flagged)
        self.assertFalse(order.mv_unvalidated)
