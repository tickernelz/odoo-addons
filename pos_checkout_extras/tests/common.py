from odoo.addons.point_of_sale.tests.common import TestPoSCommon


class CheckoutExtrasCommon(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.product = cls.create_product('Checkout Product', cls.categ_basic, 100.0)
        cls.branch_plan = cls.env['account.analytic.plan'].create({'name': 'Branch'})
        cls.channel_plan = cls.env['account.analytic.plan'].create({'name': 'Channel'})
        cls.ubud = cls.env['account.analytic.account'].create({'name': 'Ubud', 'plan_id': cls.branch_plan.id})
        cls.seminyak = cls.env['account.analytic.account'].create({'name': 'Seminyak', 'plan_id': cls.branch_plan.id})
        cls.retail = cls.env['account.analytic.account'].create({'name': 'Retail', 'plan_id': cls.channel_plan.id})
        cls.ubud_retail = {str(cls.ubud.id): 100.0, str(cls.retail.id): 100.0}
        cls.seminyak_only = {str(cls.seminyak.id): 100.0}

    def order_data(self, customer=False, payments=None, quantity=1, **values):
        return self.create_ui_order_data(
            [(self.product, quantity)], customer=customer, payments=payments, pos_order_ui_args=values)

    def sync(self, *orders):
        result = self.env['pos.order'].sync_from_ui(list(orders))
        uuids = {order['uuid'] for order in orders}
        return self.env['pos.order'].browse([order['id'] for order in result['pos.order'] if order['uuid'] in uuids])

    def close_session(self, cash_difference=0.0, bank_diffs=None):
        session = self.pos_session
        cash_method = session.payment_method_ids.filtered(lambda method: method.is_cash_count and not method.split_transactions)[:1]
        expected = session.cash_register_balance_start + sum(
            session.order_ids.payment_ids.filtered(lambda payment: payment.payment_method_id == cash_method).mapped('amount')
        ) + sum(session.statement_line_ids.mapped('amount'))
        session.post_closing_cash_details(expected + cash_difference)
        result = session.close_session_from_ui(bank_payment_method_diff_pairs=bank_diffs or [])
        self.assertTrue(result.get('successful'), result)
        self.assertEqual(session.state, 'closed')
        return session

    def session_lines(self, session):
        payments = self.env['account.payment'].search([('pos_session_id', '=', session.id)])
        diff_moves = self.env['account.move'].search([('ref', 'like', '(%s)' % session.name), ('move_type', '=', 'entry')])
        moves = session.move_id | session.statement_line_ids.move_id | payments.move_id | diff_moves
        return moves.line_ids.filtered(lambda line: line.display_type not in ('line_section', 'line_note'))

    @staticmethod
    def profit_and_loss(lines):
        return lines.filtered(lambda line: line.account_id.internal_group in ('income', 'expense'))
