from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestReports(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outlet_1 = cls.env['pos.config'].create({'name': 'Outlet 1'})
        cls.outlet_2 = cls.env['pos.config'].create({'name': 'Outlet 2'})

    def make_usage(self, program=None, date='2026-03-10 10:00:00', bank_share=100000.0,
                   discount=100000.0, config=None, **overrides):
        values = {
            'program_id': (program or self.program).id,
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

    def daily_totals(self):
        groups = self.env['pos.bank.card.usage']._read_group(
            [('state', '=', 'confirmed')], ['date:day'],
            ['discount_amount:sum', 'bank_share_amount:sum'])
        return {day: (discount, bank_share) for day, discount, bank_share in groups}

    def test_scenario_8_graph_totals_grow_with_two_transactions(self):
        before = self.daily_totals()
        self.make_usage(discount=100000.0, bank_share=100000.0)
        self.make_usage(discount=60000.0, bank_share=60000.0)
        after = self.daily_totals()
        day = datetime(2026, 3, 10)
        self.assertEqual(after[day][0] - before.get(day, (0.0, 0.0))[0], 160000.0)
        self.assertEqual(after[day][1] - before.get(day, (0.0, 0.0))[1], 160000.0)

    def test_usage_carries_its_bank_for_filters(self):
        usage = self.make_usage()
        self.assertEqual(usage.bank_id, self.bank_a)
        half = self.make_usage(program=self.program_half, card_bin='900350')
        self.assertEqual(half.bank_id, self.bank_b)

    def test_outlet_recap_groups_bank_share_per_outlet(self):
        self.make_usage(config=self.outlet_1, bank_share=30000.0)
        self.make_usage(config=self.outlet_1, bank_share=20000.0)
        self.make_usage(config=self.outlet_2, bank_share=5000.0)
        groups = self.env['pos.bank.card.usage']._read_group(
            [('state', '=', 'confirmed'), ('program_id', '=', self.program.id)],
            ['config_id'], ['bank_share_amount:sum'])
        per_outlet = {config: total for config, total in groups}
        self.assertEqual(per_outlet[self.outlet_1], 50000.0)
        self.assertEqual(per_outlet[self.outlet_2], 5000.0)

    def test_outstanding_aging_buckets(self):
        now = fields.Datetime.now()
        self.make_usage(date=now - timedelta(days=5, hours=1), bank_share=100.0, discount=100.0)
        self.make_usage(date=now - timedelta(days=40, hours=1), bank_share=200.0, discount=200.0)
        self.make_usage(date=now - timedelta(days=90, hours=1), bank_share=400.0, discount=400.0)
        self.make_usage(
            date=now - timedelta(days=5, hours=1), bank_share=800.0, discount=800.0,
            claim_state='paid')
        self.make_usage(
            date=now - timedelta(days=90, hours=1), bank_share=1600.0, discount=1600.0,
            state='cancelled', claim_state='cancelled')
        self.env.flush_all()
        row = self.env['pos.bank.card.outstanding'].search([('bank_id', '=', self.bank_a.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.usage_count, 3)
        self.assertEqual(row.amount_0_30, 100.0)
        self.assertEqual(row.amount_31_60, 200.0)
        self.assertEqual(row.amount_over_60, 400.0)
        self.assertEqual(row.total_amount, 700.0)

    def test_claimed_but_unpaid_stays_outstanding(self):
        now = fields.Datetime.now()
        self.make_usage(
            date=now - timedelta(days=2, hours=1), bank_share=250.0, discount=250.0,
            claim_state='claimed')
        self.env.flush_all()
        row = self.env['pos.bank.card.outstanding'].search([('bank_id', '=', self.bank_a.id)])
        self.assertEqual(row.total_amount, 250.0)
        self.assertEqual(row.amount_0_30, 250.0)

    def test_report_actions_are_native_views(self):
        expectations = {
            'pos_bank_card_program.action_pos_bank_card_usage': ('list', 'pivot', 'graph', 'form'),
            'pos_bank_card_program.action_pos_bank_card_outstanding': ('list', 'pivot'),
            'pos_bank_card_program.action_pos_bank_card_quota_usage': ('list', 'pivot'),
            'pos_bank_card_program.action_pos_bank_card_claim': ('list', 'pivot', 'form'),
        }
        for xmlid, modes in expectations.items():
            action = self.env.ref(xmlid)
            for mode in modes:
                self.assertIn(mode, action.view_mode.split(','), xmlid)
        self.assertEqual(
            self.env.ref('pos_bank_card_program.action_bank_card_dashboard').tag,
            'pos_bank_card_program.dashboard')

    def test_dashboard_totals_outlets_and_quota(self):
        self.program.write({'quota_type': 'count', 'quota_period': 'day', 'quota_limit_count': 4})
        day = fields.Date.today()
        when = fields.Datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        quota = self.program._quota_for(day, create=True)
        quota.used_count = 3
        self.make_usage(date=when, config=self.outlet_1, discount=100000.0, bank_share=100000.0)
        self.make_usage(date=when, config=self.outlet_2, discount=60000.0, bank_share=30000.0)
        self.make_usage(date=when, config=self.outlet_2, discount=50000.0, bank_share=50000.0,
                        state='cancelled')
        self.make_usage(program=self.program_half, card_bin='900350', date=when, discount=999.0,
                        bank_share=999.0)
        data = self.env['pos.bank.card.dashboard'].with_context(tz='UTC').get_dashboard(
            date_from=day, date_to=day, bank_id=self.bank_a.id)
        kpis = {kpi['key']: kpi['value'] for kpi in data['kpis']}
        self.assertEqual(kpis['usage'], '2')
        self.assertIn('160,000.00', kpis['discount'])
        self.assertIn('130,000.00', kpis['bank_share'])
        self.assertEqual([row['name'] for row in data['outlets']], ['Outlet 1', 'Outlet 2'])
        self.assertEqual(data['outlets'][0]['share_percent'], 76.9)
        [quota_row] = [row for row in data['quotas'] if row['id'] == self.program.id]
        self.assertEqual((quota_row['used'], quota_row['limit'], quota_row['percent']), ('3', '4', 75.0))
        self.assertEqual(sum(day_row['discount'] for day_row in data['days']), 160000.0)

    def test_dashboard_hides_claims_from_users_without_claim_access(self):
        manager = self.env['res.users'].create({
            'name': 'dash_manager', 'login': 'dash_manager',
            'group_ids': [(4, self.env.ref('base.group_user').id),
                          (4, self.env.ref('pos_bank_card_program.group_bank_card_manager').id)]})
        cashier = self.env['res.users'].create({
            'name': 'dash_cashier', 'login': 'dash_cashier',
            'group_ids': [(4, self.env.ref('base.group_user').id)]})
        keys = [kpi['key'] for kpi in self.env['pos.bank.card.dashboard'].with_user(manager).get_dashboard()['kpis']]
        self.assertIn('usage', keys)
        self.assertIn('claims', keys)
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.dashboard'].with_user(cashier).get_dashboard()

    def test_programme_validity_label(self):
        self.assertEqual(self.program.validity_label, 'No limit')
        self.program.date_to = '2026-12-31'
        self.assertIn('Until', self.program.validity_label)
