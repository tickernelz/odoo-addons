from odoo.tests import tagged

from .common import ManagerValidationCommon


@tagged('post_install', '-at_install')
class TestManagerValidationTour(ManagerValidationCommon):

    def test_point_of_sale_without_validation_is_unchanged(self):
        session = self.open_session()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'pos_mv_off_tour', login='pos_user')
        order = self.env['pos.order'].search([('session_id', '=', session.id)])
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.lines.discount, 5.0)
        self.assertFalse(order.mv_flagged)
        self.assertEqual(self.Validation.search_count([]), 0)

    def test_required_customer_comes_before_the_payment_pin(self):
        if 'customer_required_mode' not in self.main_pos_config._fields:
            self.skipTest('pos_checkout_extras is not installed')
        self.guard('payment')
        self.main_pos_config.customer_required_mode = 'payment'
        session = self.open_session()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'pos_mv_with_required_customer_tour', login='pos_user')
        order = self.env['pos.order'].search([('session_id', '=', session.id)])
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.partner_id.name, 'Deco Addict')
        self.assertEqual(self.Validation.search([]).mapped('action'), ['payment'])

    def test_guarded_actions_wait_for_the_manager(self):
        self.guard('discount', 'delete_line', 'payment')
        session = self.open_session()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'pos_mv_guarded_tour', login='pos_user')
        order = self.env['pos.order'].search([('session_id', '=', session.id)])
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.lines.product_id, self.product)
        self.assertFalse(order.mv_flagged)
        rows = self.Validation.search([])
        self.assertEqual(
            sorted((row.action, row.success) for row in rows),
            [('delete_line', True), ('discount', False), ('discount', True), ('payment', True)])
        self.assertEqual(set(rows.employee_id.ids), set(self.manager.ids))
        self.assertEqual(rows.filtered(lambda row: row.action == 'payment').reference, order.uuid)
