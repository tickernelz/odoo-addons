import base64
import io
from datetime import datetime

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestClaim(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outlet_1 = cls.env['pos.config'].create({'name': 'Outlet 1'})
        cls.outlet_2 = cls.env['pos.config'].create({'name': 'Outlet 2'})
        cls.income_account = cls.env['account.account'].create({
            'name': 'Bank Card Claim Income', 'code': 'BCCINC', 'account_type': 'income_other'})
        cls.receivable_account = cls.env['account.account'].create({
            'name': 'Bank Card Claim Receivable', 'code': 'BCCREC',
            'account_type': 'asset_receivable', 'reconcile': True})
        cls.sale_journal = cls.env['account.journal'].create({
            'name': 'Bank Card Sales', 'code': 'BCCSJ', 'type': 'sale'})
        cls.bank_journal = cls.env['account.journal'].create({
            'name': 'Bank Card Bank', 'code': 'BCCBJ', 'type': 'bank'})
        template = cls.env.ref('pos_bank_card_program.product_bank_card_claim_template')
        template.property_account_income_id = cls.income_account
        cls.bank_a.property_account_receivable_id = cls.receivable_account
        cls.bank_b.property_account_receivable_id = cls.receivable_account
        cls.finance = cls.make_user('claim_finance', 'pos_bank_card_program.group_bank_card_finance')
        cls.finance_billing = cls.make_user(
            'claim_finance_billing', 'pos_bank_card_program.group_bank_card_finance',
            'account.group_account_invoice')
        cls.finance_jakarta = cls.make_user(
            'claim_finance_jakarta', 'pos_bank_card_program.group_bank_card_finance', tz='Asia/Jakarta')
        cls.manager = cls.make_user('claim_manager', 'pos_bank_card_program.group_bank_card_manager')
        cls.cashier = cls.make_user('claim_cashier', 'point_of_sale.group_pos_user')

    @classmethod
    def make_user(cls, login, *group_xmlids, **values):
        groups = [(4, cls.env.ref('base.group_user').id)]
        groups += [(4, cls.env.ref(xmlid).id) for xmlid in group_xmlids]
        values.update({'name': login, 'login': login, 'group_ids': groups})
        return cls.env['res.users'].create(values)

    def make_usage(self, program=None, date='2026-01-15 10:00:00', bank_share=100000.0,
                   discount=100000.0, config=None, **overrides):
        program = program or self.program
        values = {
            'program_id': program.id,
            'date': date,
            'config_id': config.id if config else False,
            'card_bin': '900150',
            'card_last4': '4242',
            'approval_code': 'AP1234',
            'basis_amount': 1000000.0,
            'discount_amount': discount,
            'bank_share_amount': bank_share,
            'store_share_amount': discount - bank_share,
            'state': 'confirmed',
        }
        values.update(overrides)
        return self.env['pos.bank.card.usage'].create(values)

    def make_wizard(self, bank=None, program=None, date_from='2026-01-01', date_to='2026-01-31'):
        return self.env['pos.bank.card.claim.wizard'].create({
            'bank_id': (bank or self.bank_a).id,
            'program_id': program.id if program else False,
            'date_from': date_from,
            'date_to': date_to,
        })

    def run_wizard(self, **kwargs):
        wizard = self.make_wizard(**kwargs)
        wizard.action_preview()
        action = wizard.action_create_claim()
        return self.env['pos.bank.card.claim'].browse(action['res_id'])

    def test_claim_holding_usage_cannot_be_deleted(self):
        self.make_usage()
        claim = self.run_wizard()
        self.assertEqual(set(claim.usage_ids.mapped('claim_state')), {'claimed'})
        with self.assertRaises(UserError):
            claim.with_user(self.finance).unlink()
        self.assertTrue(claim.exists())
        claim.action_cancel()
        self.assertEqual(set(claim.usage_ids.mapped('claim_state')), set())
        claim.with_user(self.finance).unlink()
        self.assertFalse(claim.exists())

    def test_scenario_7_wizard_excel_and_invoice_totals_match_usage(self):
        first = self.make_usage(config=self.outlet_1, bank_share=100000.0, discount=100000.0)
        second = self.make_usage(config=self.outlet_2, bank_share=50000.0, discount=50000.0)
        cancelled = self.make_usage(
            bank_share=99999.0, state='cancelled', claim_state='cancelled')
        outside = self.make_usage(date='2026-02-02 10:00:00', bank_share=77777.0)
        other_bank = self.make_usage(program=self.program_half, bank_share=88888.0)

        wizard = self.make_wizard()
        wizard.action_preview()
        self.assertEqual(wizard.state, 'preview')
        self.assertEqual(wizard.usage_count, 2)
        self.assertEqual(wizard.total_bank_share_amount, 150000.0)
        per_outlet = {line.config_id: line.bank_share_amount for line in wizard.line_ids}
        self.assertEqual(per_outlet, {self.outlet_1: 100000.0, self.outlet_2: 50000.0})

        action = wizard.action_create_claim()
        claim = self.env['pos.bank.card.claim'].browse(action['res_id'])
        self.assertEqual(claim.bank_id, self.bank_a)
        self.assertEqual(claim.usage_count, 2)
        self.assertEqual(claim.total_amount, 150000.0)
        self.assertEqual(claim.total_discount_amount, 150000.0)
        self.assertEqual(set(claim.usage_ids.ids), {first.id, second.id})
        self.assertEqual(set(claim.usage_ids.mapped('claim_state')), {'claimed'})
        self.assertEqual(cancelled.claim_state, 'cancelled')
        self.assertFalse(cancelled.claim_id)
        self.assertEqual(outside.claim_state, 'not_claimed')
        self.assertEqual(other_bank.claim_state, 'not_claimed')

        claim.action_export_xlsx()
        self.assertTrue(claim.export_file)
        excel_total = self.read_excel_bank_share(claim)
        self.assertEqual(excel_total, 150000.0)
        self.assertEqual(excel_total, sum(claim.usage_ids.mapped('bank_share_amount')))

        claim.action_create_invoice()
        invoice = claim.invoice_id
        self.assertTrue(invoice)
        self.assertEqual(invoice.partner_id, self.bank_a)
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.amount_total, 150000.0)
        self.assertEqual(invoice.amount_total, excel_total)
        with self.assertRaises(UserError):
            claim.action_create_invoice()

    def read_excel_bank_share(self, claim):
        import openpyxl

        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(claim.export_file)), read_only=True, data_only=True)
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        column = headers.index('Bank Share') + 1
        total = 0.0
        total_row = 0.0
        for row in sheet.iter_rows(min_row=2):
            label = row[0].value
            value = row[column - 1].value or 0.0
            if label == 'Total':
                total_row = value
            else:
                total += value
        self.assertEqual(total_row, total)
        return total

    def test_wizard_without_matches_fails(self):
        wizard = self.make_wizard(date_from='2025-01-01', date_to='2025-01-31')
        wizard.action_preview()
        self.assertEqual(wizard.usage_count, 0)
        with self.assertRaises(UserError):
            wizard.action_create_claim()

    def test_one_bank_one_period(self):
        self.make_usage()
        self.run_wizard()
        with self.assertRaises(ValidationError):
            self.env['pos.bank.card.claim'].create({
                'bank_id': self.bank_a.id, 'date_from': '2026-01-15', 'date_to': '2026-02-15'})
        february = self.env['pos.bank.card.claim'].create({
            'bank_id': self.bank_a.id, 'date_from': '2026-02-01', 'date_to': '2026-02-28'})
        self.assertTrue(february.id)

    def test_cancelled_claim_frees_the_period(self):
        usage = self.make_usage()
        claim = self.run_wizard()
        self.assertEqual(usage.claim_state, 'claimed')
        claim.action_cancel()
        self.assertEqual(claim.state, 'cancelled')
        self.assertEqual(usage.claim_state, 'not_claimed')
        self.assertFalse(usage.claim_id)
        second = self.run_wizard()
        self.assertEqual(usage.claim_id, second)

    def test_rejected_claim_releases_usage_for_a_new_claim(self):
        usage = self.make_usage()
        claim = self.run_wizard()
        claim.action_send()
        claim.action_reject()
        self.assertEqual(claim.state, 'rejected')
        self.assertEqual(usage.claim_state, 'not_claimed')
        self.assertFalse(usage.claim_id)
        second = self.run_wizard()
        self.assertEqual(usage.claim_id, second)

    def test_program_must_belong_to_the_claim_bank(self):
        with self.assertRaises(ValidationError):
            self.env['pos.bank.card.claim'].create({
                'bank_id': self.bank_a.id,
                'program_id': self.program_half.id,
                'date_from': '2026-03-01',
                'date_to': '2026-03-31',
            })

    def test_mark_paid_without_invoice_and_reconcile_with_paid_invoice(self):
        usage = self.make_usage()
        claim = self.run_wizard()
        claim.action_send()
        with self.assertRaises(UserError):
            claim.action_reconcile()
        claim.action_mark_paid()
        self.assertEqual(claim.state, 'paid')
        self.assertEqual(usage.claim_state, 'paid')

        february_usage = self.make_usage(date='2026-02-10 10:00:00', bank_share=40000.0)
        invoiced = self.run_wizard(date_from='2026-02-01', date_to='2026-02-28')
        invoiced.action_create_invoice()
        invoiced.action_send()
        with self.assertRaises(UserError):
            invoiced.action_mark_paid()
        with self.assertRaises(UserError):
            invoiced.action_reconcile()
        self.pay_invoice(invoiced.invoice_id)
        invoiced.action_reconcile()
        self.assertEqual(invoiced.state, 'paid')
        self.assertEqual(february_usage.claim_state, 'paid')

    def pay_invoice(self, invoice, user=None):
        if user:
            invoice = invoice.with_user(user)
        invoice.action_post()
        register = self.env['account.payment.register']
        if user:
            register = register.with_user(user)
        register = register.with_context(
            active_model='account.move', active_ids=invoice.ids).create({
                'journal_id': self.bank_journal.id})
        register.action_create_payments()
        self.assertEqual(invoice.payment_state, 'paid')

    def test_cancel_with_open_invoice_is_refused(self):
        self.make_usage()
        claim = self.run_wizard()
        claim.action_create_invoice()
        with self.assertRaises(UserError):
            claim.action_cancel()
        claim.invoice_id.write({'state': 'cancel'})
        claim.action_cancel()
        self.assertEqual(claim.state, 'cancelled')

    def test_excel_dates_use_the_users_timezone(self):
        usage = self.make_usage(date='2026-01-31 18:30:00')
        january = self.make_wizard().with_user(self.finance_jakarta)
        january.action_preview()
        self.assertEqual(january.usage_count, 0)
        february = self.make_wizard(
            date_from='2026-02-01', date_to='2026-02-28').with_user(self.finance_jakarta)
        february.action_preview()
        self.assertEqual(february.usage_count, 1)
        claim = self.env['pos.bank.card.claim'].browse(
            february.action_create_claim()['res_id']).with_user(self.finance_jakarta)
        claim.action_export_xlsx()
        self.assertEqual(self.read_excel_dates(claim), [datetime(2026, 2, 1, 1, 30)])
        self.assertEqual(usage.claim_id.id, claim.id)

    def read_excel_dates(self, claim):
        import openpyxl

        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(claim.export_file)), read_only=True, data_only=True)
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        column = headers.index('Date')
        return [
            row[column].value for row in sheet.iter_rows(min_row=2)
            if isinstance(row[column].value, datetime)
        ]

    def test_export_survives_colliding_sheet_names(self):
        import openpyxl

        self.make_usage()
        slashed = self.make_programme(name='Promo A/B')
        plain = self.make_programme(name='Promo AB')
        self.make_usage(program=slashed, card_bin='900550')
        self.make_usage(program=plain, card_bin='900550')
        claim = self.run_wizard()
        claim.action_export_xlsx()
        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(claim.export_file)), read_only=True)
        names = workbook.sheetnames
        self.assertEqual(len(names), 3)
        self.assertEqual(len({name.lower() for name in names}), 3)

    def test_finance_with_billing_without_pos_groups_completes_the_flow(self):
        user = self.finance_billing
        usage = self.make_usage(config=self.outlet_1)
        with self.assertRaises(AccessError):
            usage.with_user(user).config_id.name
        [row] = usage.with_user(user).web_read({'config_id': {'fields': {'display_name': {}}}})
        self.assertEqual(row['config_id'], {'id': self.outlet_1.id, 'display_name': 'Outlet 1'})
        visible = self.env['ir.ui.menu'].with_user(user)._visible_menu_ids()
        self.assertIn(
            self.env.ref('pos_bank_card_program.menu_pos_bank_card_claim_accounting').id, visible)
        self.assertIn(
            self.env.ref('pos_bank_card_program.menu_pos_bank_card_claim_wizard_accounting').id,
            visible)
        self.assertIn(
            self.env.ref('pos_bank_card_program.menu_pos_bank_card_outstanding_accounting').id,
            visible)
        wizard = self.make_wizard().with_user(user)
        wizard.action_preview()
        self.assertEqual(wizard.usage_count, 1)
        claim = self.env['pos.bank.card.claim'].browse(
            wizard.action_create_claim()['res_id']).with_user(user)
        claim.action_export_xlsx()
        self.assertTrue(claim.export_file)
        claim.action_create_invoice()
        claim.action_send()
        self.pay_invoice(claim.invoice_id, user=user)
        claim.action_reconcile()
        self.assertEqual(claim.state, 'paid')
        self.assertEqual(usage.claim_state, 'paid')

    def test_claim_access_by_role(self):
        self.make_usage()
        wizard = self.env['pos.bank.card.claim.wizard'].with_user(self.finance).create({
            'bank_id': self.bank_a.id, 'date_from': '2026-01-01', 'date_to': '2026-01-31'})
        wizard.action_preview()
        self.assertEqual(wizard.usage_count, 1)
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.claim.wizard'].with_user(self.manager).create({
                'bank_id': self.bank_a.id, 'date_from': '2026-01-01', 'date_to': '2026-01-31'})
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.claim.wizard'].with_user(self.cashier).create({
                'bank_id': self.bank_a.id, 'date_from': '2026-01-01', 'date_to': '2026-01-31'})
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.claim'].with_user(self.cashier).search([])
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.outstanding'].with_user(self.cashier).search([])
        self.env.flush_all()
        self.assertEqual(
            self.env['pos.bank.card.outstanding'].with_user(self.finance).search(
                [('bank_id', '=', self.bank_a.id)]).total_amount, 100000.0)
