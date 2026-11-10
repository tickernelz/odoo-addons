from datetime import datetime, timedelta

from odoo import fields
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
            'pos_bank_card_program.action_pos_bank_card_dashboard': ('graph', 'pivot'),
            'pos_bank_card_program.action_pos_bank_card_detail': ('list', 'form'),
            'pos_bank_card_program.action_pos_bank_card_outlet_recap': ('pivot', 'graph'),
            'pos_bank_card_program.action_pos_bank_card_outstanding': ('list', 'pivot'),
            'pos_bank_card_program.action_pos_bank_card_quota_usage': ('pivot', 'list'),
            'pos_bank_card_program.action_pos_bank_card_claim': ('list', 'pivot', 'form'),
        }
        for xmlid, modes in expectations.items():
            action = self.env.ref(xmlid)
            for mode in modes:
                self.assertIn(mode, action.view_mode.split(','), xmlid)
