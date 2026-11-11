from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestCoverageReport(CheckoutExtrasCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config.write({
            'payment_method_ids': [(6, 0, (cls.cash_pm1 | cls.bank_pm1).ids)],
            'analytic_distribution': cls.ubud_retail,
        })
        cls.today = fields.Date.context_today(cls.env.user)

    def test_coverage_counts_match_the_journal_items(self):
        self.open_new_session()
        self.sync(self.order_data(), self.order_data(payments=[(self.bank_pm1, 100.0)]))
        session = self.close_session()
        pnl = self.profit_and_loss(self.session_lines(session))
        pnl[0].analytic_distribution = False
        wizard = self.env['pos.analytic.coverage.wizard'].create({
            'date_from': self.today, 'date_to': self.today, 'config_ids': [(6, 0, self.config.ids)]})
        rows, totals = wizard._coverage()
        self.assertEqual(rows, [{
            'name': self.config.name,
            'distribution': 'Ubud 100.0% / Retail 100.0%',
            'scope': 'Profit and loss lines',
            'sessions': 1,
            'items': len(pnl),
            'stamped': len(pnl) - 1,
            'missing': 1,
            'other': 0,
            'coverage': round(100.0 * (len(pnl) - 1) / len(pnl)),
        }])
        self.assertEqual(totals['items'], len(pnl))

        report = self.env.ref('pos_checkout_extras.action_report_pos_analytic_coverage')
        html = report._render_qweb_html(report.id, wizard.ids)[0].decode()
        self.assertIn('POS Analytic Coverage', html)
        self.assertIn('Ubud 100.0% / Retail 100.0%', html)


@tagged('post_install', '-at_install')
class TestCoveragePdf(HttpCase):

    def test_coverage_report_renders_a_pdf(self):
        today = fields.Date.context_today(self.env.user)
        wizard = self.env['pos.analytic.coverage.wizard'].create({'date_from': today, 'date_to': today})
        report = self.env.ref('pos_checkout_extras.action_report_pos_analytic_coverage')
        pdf = report.with_context(force_report_rendering=True)._render_qweb_pdf(report.id, wizard.ids)[0]
        self.assertTrue(pdf.startswith(b'%PDF'))
