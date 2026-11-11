from odoo.tests import tagged

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestSessionAnalytic(CheckoutExtrasCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cash_journal = cls.cash_pm1.journal_id
        cls.bank_journal = cls.bank_pm1.journal_id
        cls.gain_account = cls.company_data['company'].default_cash_difference_income_account_id
        cls.loss_account = cls.company_data['company'].default_cash_difference_expense_account_id
        (cls.cash_journal | cls.bank_journal | cls.bank_split_pm1.journal_id).write({
            'profit_account_id': cls.gain_account.id,
            'loss_account_id': cls.loss_account.id,
        })
        cls.config.write({
            'payment_method_ids': [(6, 0, (cls.cash_pm1 | cls.bank_pm1 | cls.bank_split_pm1).ids)],
            'analytic_distribution': cls.ubud_retail,
        })

    def every_source_session(self):
        self.open_new_session(opening_cash=100.0)
        self.sync(
            self.order_data(),
            self.order_data(payments=[(self.bank_pm1, 100.0)]),
            self.order_data(customer=self.customer, payments=[(self.bank_split_pm1, 100.0)]),
        )
        self.pos_session.try_cash_in_out('out', 10.0, 'Ice', False, {'formattedAmount': '10.00', 'translatedType': 'out'})
        return self.close_session(
            cash_difference=-5.0,
            bank_diffs=[(self.bank_pm1.id, 2.0), (self.bank_split_pm1.id, -1.5)],
        )

    def test_every_profit_and_loss_item_of_a_closed_session_is_stamped(self):
        session = self.every_source_session()
        lines = self.session_lines(session)
        pnl = self.profit_and_loss(lines)
        self.assertFalse(pnl.filtered(lambda line: line.analytic_distribution != self.ubud_retail))
        self.assertFalse((lines - pnl).filtered('analytic_distribution'))
        stamped_accounts = pnl.mapped('account_id')
        self.assertIn(self.sales_account, stamped_accounts)
        self.assertIn(self.gain_account, stamped_accounts)
        self.assertIn(self.loss_account, stamped_accounts)
        self.assertEqual(len(pnl.filtered(lambda line: line.account_id == self.loss_account)), 2)
        self.assertEqual(
            self.env['account.analytic.line'].search_count([('move_line_id', 'in', pnl.ids)]),
            2 * len(pnl),
        )

    def test_all_lines_scope_stamps_every_item(self):
        self.config.analytic_scope = 'all'
        session = self.every_source_session()
        lines = self.session_lines(session)
        self.assertGreater(len(lines), len(self.profit_and_loss(lines)))
        self.assertFalse(lines.filtered(lambda line: line.analytic_distribution != self.ubud_retail))

    def test_invoiced_order_product_lines_carry_the_distribution(self):
        self.open_new_session()
        order = self.sync(self.create_ui_order_data(
            [(self.product, 2)], customer=self.customer, is_invoiced=True))
        invoice = order.account_move
        self.assertTrue(invoice)
        self.assertEqual(invoice.invoice_line_ids.mapped('analytic_distribution'), [self.ubud_retail])

    def test_counter_without_distribution_closes_under_a_mandatory_plan(self):
        self.branch_plan.write({'applicability_ids': [(0, 0, {
            'business_domain': 'general',
            'applicability': 'mandatory',
        })]})
        self.config.analytic_distribution = False
        session = self.every_source_session()
        lines = self.session_lines(session)
        self.assertFalse(lines.filtered('analytic_distribution'))
        self.assertFalse(self.env['account.analytic.line'].search_count([('move_line_id', 'in', lines.ids)]))

    def test_new_distribution_applies_to_later_sessions_and_is_logged(self):
        first = self.every_source_session()
        self.config.analytic_distribution = self.seminyak_only
        log = self.env['pos.analytic.sync.log'].search([('config_id', '=', self.config.id), ('kind', '=', 'distribution')], limit=1)
        self.assertEqual(log.old_distribution, self.ubud_retail)
        self.assertEqual(log.new_distribution, self.seminyak_only)
        self.assertEqual(log.user_id, self.env.user)
        second = self.every_source_session()
        self.assertEqual(set(map(str, self.profit_and_loss(self.session_lines(first)).mapped('analytic_distribution'))), {str(self.ubud_retail)})
        self.assertEqual(set(map(str, self.profit_and_loss(self.session_lines(second)).mapped('analytic_distribution'))), {str(self.seminyak_only)})

    def test_distribution_must_cover_a_mandatory_plan(self):
        self.branch_plan.write({'applicability_ids': [(0, 0, {
            'business_domain': 'general',
            'applicability': 'mandatory',
        })]})
        with self.assertRaisesRegex(Exception, '100%'):
            self.config.analytic_distribution = {str(self.ubud.id): 50.0}
