from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MANAGER_PIN, ManagerValidationCommon


@tagged('post_install', '-at_install')
class TestServerGuards(ManagerValidationCommon):

    def test_closing_needs_a_validation(self):
        self.guard('close_session')
        session = self.open_session()
        with self.assertRaises(UserError):
            session.with_user(self.pos_user).action_pos_session_closing_control()
        self.verify(session, 'close_session', MANAGER_PIN, reference=session._mv_reference())
        session.with_user(self.pos_user).action_pos_session_closing_control()
        self.assertEqual(session.state, 'closed')
        self.assertTrue(self.Validation.search([('action', '=', 'close_session')]).consumed)

    def test_backend_closing_goes_through_the_wizard(self):
        self.guard('close_session')
        session = self.open_session()
        action = session.with_user(self.pos_admin).action_mv_closing_control()
        wizard = self.env['pos.manager.validation.wizard'].with_user(self.pos_admin).browse(action['res_id'])
        wizard.write({'employee_id': self.manager.id, 'pin': '111111'})
        self.assertFalse(wizard.validated)
        self.assertIn('4', wizard.message)
        self.assertEqual(wizard.action_confirm()['res_id'], wizard.id)
        self.assertNotEqual(session.state, 'closed')
        wizard.write({'pin': MANAGER_PIN})
        wizard.action_confirm()
        self.assertEqual(session.state, 'closed')

    def test_wizard_refuses_other_methods(self):
        self.guard('close_session')
        session = self.open_session()
        action = session.with_user(self.pos_admin).action_mv_closing_control()
        wizard = self.env['pos.manager.validation.wizard'].browse(action['res_id'])
        wizard.sudo().write({'method': 'unlink', 'employee_id': self.manager.id})
        wizard.with_user(self.pos_admin).write({'pin': MANAGER_PIN})
        with self.assertRaises(AccessError):
            wizard.with_user(self.pos_admin).action_confirm()
        self.assertTrue(session.exists())

    def test_cash_moves_need_one_validation_each(self):
        self.guard('cash_move')
        session = self.open_session()
        cash_session = session.with_user(self.pos_admin)
        with self.assertRaises(UserError):
            cash_session.try_cash_in_out('out', 10.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '10'})
        self.verify(session, 'cash_move', MANAGER_PIN, reference=session._mv_reference())
        cash_session.try_cash_in_out('out', 10.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '10'})
        with self.assertRaises(UserError):
            cash_session.try_cash_in_out('out', 10.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '10'})

    def test_manager_at_the_register_is_not_asked_again(self):
        self.guard('cash_move', 'close_session')
        session = self.open_session()
        session.employee_id = self.manager
        self.verify(session, 'cash_move', MANAGER_PIN, reference=session._mv_reference(), remember=True)
        cash_session = session.with_user(self.pos_admin)
        cash_session.try_cash_in_out('out', 10.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '10'})
        cash_session.try_cash_in_out('out', 5.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '5'})
        session.with_user(self.pos_user).write({'employee_id': self.cashier.id})
        with self.assertRaises(UserError):
            cash_session.try_cash_in_out('out', 5.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '5'})
        session.with_user(self.pos_user).write({'employee_id': self.manager.id})
        with self.assertRaises(UserError):
            cash_session.try_cash_in_out('out', 5.0, 'Ice', False, {'translatedType': 'out', 'formattedAmount': '5'})

    def test_backend_refund_needs_a_validation(self):
        self.guard('refund')
        session = self.open_session()
        order = self.sync(session, self.order_data(session))
        with self.assertRaises(UserError):
            order.with_user(self.pos_admin).refund()
        action = order.with_user(self.pos_admin).action_mv_refund()
        self.assertEqual(action['res_model'], 'pos.manager.validation.wizard')
        wizard = self.env['pos.manager.validation.wizard'].with_user(self.pos_admin).browse(action['res_id'])
        wizard.write({'employee_id': self.manager.id, 'pin': MANAGER_PIN})
        result = wizard.action_confirm()
        self.assertEqual(result['res_model'], 'pos.order')
        self.assertTrue(self.env['pos.order'].browse(result['res_id']).lines.refunded_orderline_id)

    def test_backend_payment_needs_a_validation(self):
        self.guard('payment')
        session = self.open_session()
        data = self.order_data(session)
        data.update({'state': 'draft', 'payment_ids': [], 'amount_paid': 0})
        order = self.sync(session, data)
        context = {'active_id': order.id, 'active_ids': order.ids, 'active_model': 'pos.order'}
        payment = self.env['pos.make.payment'].with_user(self.pos_admin).with_context(context).create({
            'amount': order.amount_total, 'payment_method_id': session.config_id.payment_method_ids[:1].id})
        with self.assertRaises(UserError):
            payment.check()
        action = payment.action_mv_check()
        wizard = self.env['pos.manager.validation.wizard'].with_user(self.pos_admin).browse(action['res_id'])
        wizard.write({'employee_id': self.manager.id, 'pin': MANAGER_PIN})
        wizard.action_confirm()
        self.assertEqual(order.state, 'paid')

    def test_deleting_an_order_with_lines_needs_a_validation(self):
        self.guard('delete_order')
        session = self.open_session()
        data = self.order_data(session)
        data.update({'state': 'draft', 'payment_ids': [], 'amount_paid': 0})
        order = self.sync(session, data)
        empty = self.order_data(session)
        empty.update({'state': 'draft', 'payment_ids': [], 'amount_paid': 0, 'lines': []})
        empty_order = self.sync(session, empty)
        empty_order.with_user(self.pos_user).action_pos_order_cancel()
        self.assertEqual(empty_order.state, 'cancel')
        with self.assertRaises(UserError):
            order.with_user(self.pos_user).action_pos_order_cancel()
        self.verify(session, 'delete_order', MANAGER_PIN, reference=order.uuid)
        order.with_user(self.pos_user).action_pos_order_cancel()
        self.assertEqual(order.state, 'cancel')
