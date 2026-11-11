import base64
import io

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestClaimPdf(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outlet_1 = cls.env['pos.config'].create({'name': 'Outlet 1'})
        cls.outlet_2 = cls.env['pos.config'].create({'name': 'Outlet 2'})
        cls.finance = cls.make_user('pdf_finance', 'pos_bank_card_program.group_bank_card_finance')
        cls.finance_jakarta = cls.make_user(
            'pdf_finance_jakarta', 'pos_bank_card_program.group_bank_card_finance',
            tz='Asia/Jakarta')
        cls.manager = cls.make_user('pdf_manager', 'pos_bank_card_program.group_bank_card_manager')

    @classmethod
    def make_user(cls, login, *group_xmlids, **values):
        groups = [(4, cls.env.ref('base.group_user').id)]
        groups += [(4, cls.env.ref(xmlid).id) for xmlid in group_xmlids]
        values.update({'name': login, 'login': login, 'group_ids': groups})
        return cls.env['res.users'].create(values)

    def make_usage(self, program=None, date='2026-01-15 10:00:00', bank_share=100000.0,
                   discount=100000.0, basis=1000000.0, config=None, **overrides):
        values = {
            'program_id': (program or self.program).id,
            'date': date,
            'config_id': config.id if config else False,
            'card_bin': '900150',
            'card_last4': '4242',
            'approval_code': 'AP1234',
            'basis_amount': basis,
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

    def make_claim(self):
        wizard = self.make_wizard()
        wizard.action_preview()
        return self.env['pos.bank.card.claim'].browse(wizard.action_create_claim()['res_id'])

    def render_html(self, action_xmlid, record, user):
        report = self.env.ref(action_xmlid).with_user(user)
        html, _content_type = report._render_qweb_html(report.id, [record.id])
        return html.decode() if isinstance(html, bytes) else html

    def excel_values(self, claim, header):
        import openpyxl

        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(claim.export_file)), read_only=True, data_only=True)
        amounts = []
        for sheet in workbook.worksheets:
            headers = [cell.value for cell in sheet[1]]
            column = headers.index(header)
            amounts.extend(
                row[column].value or 0.0 for row in sheet.iter_rows(min_row=2)
                if row[0].value != 'Total')
        return amounts

    def test_pdf_excel_and_preview_share_the_same_totals(self):
        first = self.make_usage(config=self.outlet_1, bank_share=100000.0, discount=120000.0, basis=800000.0)
        second = self.make_usage(config=self.outlet_2, bank_share=50000.0, discount=60000.0, basis=400000.0)
        third = self.make_usage(config=self.outlet_1, bank_share=25000.0, discount=30000.0, basis=200000.0)
        self.make_usage(date='2026-02-02 10:00:00', bank_share=77777.0)
        self.make_usage(program=self.program_half, bank_share=88888.0)
        usages = first + second + third

        wizard = self.make_wizard().with_user(self.finance)
        wizard.action_preview()
        self.assertEqual(wizard.usage_count, 3)
        self.assertEqual(wizard.total_basis_amount, sum(usages.mapped('basis_amount')))
        self.assertEqual(wizard.total_discount_amount, sum(usages.mapped('discount_amount')))
        self.assertEqual(wizard.total_bank_share_amount, sum(usages.mapped('bank_share_amount')))
        per_outlet = {line.config_id: line for line in wizard.line_ids}
        self.assertEqual(per_outlet[self.outlet_1].usage_count, 2)
        self.assertEqual(per_outlet[self.outlet_1].basis_amount, 1000000.0)
        self.assertEqual(per_outlet[self.outlet_2].bank_share_amount, 50000.0)

        wizard_data = wizard._claim_pdf_data()
        self.assertEqual(wizard_data['usage_count'], wizard.usage_count)
        self.assertEqual(len(wizard_data['details']), wizard.usage_count)
        self.assertEqual(wizard_data['totals']['basis_amount'], wizard.total_basis_amount)
        self.assertEqual(wizard_data['totals']['discount_amount'], wizard.total_discount_amount)
        self.assertEqual(wizard_data['totals']['bank_share_amount'], wizard.total_bank_share_amount)
        summary = {line['outlet']: line for line in wizard_data['summary']}
        self.assertEqual(summary['Outlet 1']['usage_count'], per_outlet[self.outlet_1].usage_count)
        self.assertEqual(summary['Outlet 1']['basis_amount'], per_outlet[self.outlet_1].basis_amount)
        self.assertIn('Outlet 1', self.render_html(
            'pos_bank_card_program.action_report_bank_card_claim_wizard', wizard, self.finance))
        action = wizard.action_print_pdf()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(
            action['url'],
            '/report/pdf/pos_bank_card_program.report_bank_card_claim_wizard/%s' % wizard.id)

        claim = self.env['pos.bank.card.claim'].browse(wizard.action_create_claim()['res_id'])
        claim_data = claim.with_user(self.finance)._claim_pdf_data()
        self.assertEqual(claim_data['usage_count'], 3)
        self.assertEqual(claim_data['totals'], wizard_data['totals'])
        self.assertEqual(len(claim_data['details']), 3)

        claim.action_export_xlsx()
        for header, code in (
                ('Basis', 'basis_amount'), ('Discount', 'discount_amount'), ('Bank Share', 'bank_share_amount')):
            amounts = self.excel_values(claim, header)
            self.assertEqual(len(amounts), 3)
            self.assertEqual(sum(amounts), claim_data['totals'][code])

    def test_claim_report_renders_masked_card_only(self):
        usage = self.make_usage(config=self.outlet_1, date='2026-01-31 18:30:00')
        claim = self.make_claim()
        jakarta_html = self.render_html(
            'pos_bank_card_program.action_report_bank_card_claim', claim, self.finance_jakarta)
        self.assertIn('Bank A', jakarta_html)
        self.assertIn('Outlet 1', jakarta_html)
        self.assertIn(claim.name, jakarta_html)
        self.assertIn(usage.masked_card, jakarta_html)
        self.assertIn('AP1234', jakarta_html)
        self.assertIn('2026-02-01 01:30', jakarta_html)
        self.assertIn('**', jakarta_html)
        self.assertNotIn('9001504242', jakarta_html)
        self.assertNotIn('Rp', jakarta_html)

    def test_claim_report_pdf_output(self):
        usage = self.make_usage(config=self.outlet_1)
        claim = self.make_claim()
        report = self.env.ref(
            'pos_bank_card_program.action_report_bank_card_claim').with_user(self.finance)
        content, content_type = report._render_qweb_pdf(report.id, [claim.id])
        if isinstance(content, str):
            content = content.encode()
        if content_type == 'pdf':
            self.assertTrue(content.startswith(b'%PDF'))
        else:
            self.assertIn(usage.masked_card.encode(), content)

    def test_empty_claim_and_wizard_fail_with_a_clear_message(self):
        claim = self.env['pos.bank.card.claim'].create({
            'bank_id': self.bank_a.id,
            'date_from': '2026-01-01',
            'date_to': '2026-01-31',
        })
        with self.assertRaises(UserError):
            claim.with_user(self.finance).action_print_pdf()
        wizard = self.make_wizard(date_from='2025-01-01', date_to='2025-01-31').with_user(self.finance)
        wizard.action_preview()
        with self.assertRaises(UserError):
            wizard.action_print_pdf()

    def test_print_is_finance_only(self):
        self.make_usage(config=self.outlet_1)
        claim = self.make_claim()
        form = self.env.ref('pos_bank_card_program.view_pos_bank_card_claim_form')
        finance_arch = self.env['pos.bank.card.claim'].with_user(self.finance).get_view(
            view_id=form.id, view_type='form')['arch']
        manager_arch = self.env['pos.bank.card.claim'].with_user(self.manager).get_view(
            view_id=form.id, view_type='form')['arch']
        self.assertIn('action_print_pdf', finance_arch)
        self.assertNotIn('action_print_pdf', manager_arch)
        with self.assertRaises(AccessError):
            claim.with_user(self.manager).action_print_pdf()
        with self.assertRaises(AccessError):
            self.render_html(
                'pos_bank_card_program.action_report_bank_card_claim', claim, self.manager)
        action = claim.with_user(self.finance).action_print_pdf()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(
            action['url'],
            '/report/pdf/pos_bank_card_program.report_bank_card_claim/%s' % claim.id)

    def test_report_works_on_demo_usage_when_loaded(self):
        demo_usage = self.env.ref(
            'pos_bank_card_program.usage_bank_a_outlet_1', raise_if_not_found=False)
        if not demo_usage:
            self.skipTest('demo data is not loaded')
        self.assertEqual(demo_usage.state, 'confirmed')
        wizard = self.env['pos.bank.card.claim.wizard'].with_user(self.finance).create({
            'bank_id': demo_usage.bank_id.id,
            'company_id': demo_usage.company_id.id,
            'date_from': demo_usage.date.date(),
            'date_to': demo_usage.date.date(),
        })
        wizard.action_preview()
        html = self.render_html(
            'pos_bank_card_program.action_report_bank_card_claim_wizard', wizard, self.finance)
        self.assertIn(demo_usage.masked_card, html)
