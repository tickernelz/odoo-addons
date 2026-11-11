from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestAnalyticSync(CheckoutExtrasCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config.write({'payment_method_ids': [(6, 0, (cls.cash_pm1 | cls.bank_pm1).ids)]})
        cls.today = fields.Date.context_today(cls.env.user)
        cls.products = cls.env['product.product'].concat(*[
            cls.create_product(
                'Sync Product %s' % index, cls.categ_basic, 100.0,
                sale_account=cls.copy_account(cls.sales_account, {'name': 'Sales %s' % index}))
            for index in range(3)
        ])

    def closed_unstamped_session(self):
        self.config.analytic_distribution = False
        self.open_new_session()
        self.sync(*[self.create_ui_order_data([(product, 1)]) for product in self.products])
        session = self.close_session()
        self.config.analytic_distribution = self.ubud_retail
        return session

    def wizard(self, **values):
        values.setdefault('config_id', self.config.id)
        values.setdefault('date_from', self.today)
        values.setdefault('date_to', self.today)
        return self.env['pos.analytic.sync.wizard'].create(values)

    def test_missing_mode_fills_only_empty_items_in_batches_and_logs(self):
        session = self.closed_unstamped_session()
        pnl = self.profit_and_loss(self.session_lines(session))
        self.assertTrue(pnl)
        hand_made = {str(self.seminyak.id): 100.0}
        pnl[0].analytic_distribution = hand_made
        wizard = self.wizard(mode='missing')
        self.assertEqual(wizard.session_count, 1)
        self.assertEqual(wizard.matched_count, len(pnl))
        self.assertEqual(wizard.pending_count, len(pnl) - 1)
        clicks = 0
        with patch.object(type(wizard), '_batch_size', 1):
            while wizard.pending_count:
                wizard.action_synchronise()
                clicks += 1
                self.assertEqual(wizard.updated_count, 1)
        self.assertEqual(clicks, len(pnl) - 1)
        self.assertEqual(pnl[0].analytic_distribution, hand_made)
        self.assertFalse(pnl[1:].filtered(lambda line: line.analytic_distribution != self.ubud_retail))
        logs = self.env['pos.analytic.sync.log'].search([('config_id', '=', self.config.id), ('kind', '=', 'sync')])
        self.assertEqual(len(logs), clicks)
        self.assertEqual(sum(logs.mapped('updated_count')), len(pnl) - 1)
        again = self.wizard(mode='missing')
        self.assertEqual(again.pending_count, 0)
        again.action_synchronise()
        self.assertEqual(again.updated_count, 0)

    def test_replace_mode_overwrites_other_distributions(self):
        session = self.closed_unstamped_session()
        pnl = self.profit_and_loss(self.session_lines(session))
        pnl[0].analytic_distribution = {str(self.seminyak.id): 100.0}
        wizard = self.wizard(mode='replace')
        self.assertEqual(wizard.overwrite_count, 1)
        self.assertEqual(wizard.pending_count, len(pnl))
        wizard.action_synchronise()
        self.assertEqual(wizard.updated_count, len(pnl))
        self.assertEqual(wizard.pending_count, 0)
        self.assertFalse(pnl.filtered(lambda line: line.analytic_distribution != self.ubud_retail))

    def test_sessions_outside_the_period_are_left_alone(self):
        session = self.closed_unstamped_session()
        wizard = self.wizard(date_from=fields.Date.add(self.today, days=1), date_to=fields.Date.add(self.today, days=2))
        self.assertEqual(wizard.matched_count, 0)
        wizard.action_synchronise()
        self.assertFalse(self.profit_and_loss(self.session_lines(session)).filtered('analytic_distribution'))

    def test_only_point_of_sale_managers_can_synchronise(self):
        cashier = new_test_user(self.env, login='checkout_cashier', groups='base.group_user,point_of_sale.group_pos_user')
        with self.assertRaises(AccessError):
            self.env['pos.analytic.sync.wizard'].with_user(cashier).create({
                'config_id': self.config.id, 'date_from': self.today, 'date_to': self.today})
        with self.assertRaises(AccessError):
            self.env['pos.analytic.sync.log'].with_user(cashier).search([])

    def test_all_counters_stay_inside_the_allowed_companies(self):
        session = self.closed_unstamped_session()
        other_company = self.setup_other_company()['company']
        wizard = self.env['pos.analytic.sync.wizard'].with_company(other_company).with_context(
            allowed_company_ids=other_company.ids).create({'date_from': self.today, 'date_to': self.today})
        self.assertEqual(wizard.matched_count, 0)
        wizard.action_synchronise()
        self.assertFalse(self.profit_and_loss(self.session_lines(session)).filtered('analytic_distribution'))
        everywhere = self.wizard(config_id=False)
        self.assertEqual(everywhere.pending_count, len(self.profit_and_loss(self.session_lines(session))))

    def test_settings_buttons_open_the_wizard_for_one_or_all_counters(self):
        settings = self.env['res.config.settings'].create({'pos_config_id': self.config.id})
        current = self.env['pos.analytic.sync.wizard'].browse(settings.action_pos_analytic_sync_current()['res_id'])
        self.assertEqual(current.config_id, self.config)
        every = self.env['pos.analytic.sync.wizard'].browse(settings.action_pos_analytic_sync_all()['res_id'])
        self.assertFalse(every.config_id)
