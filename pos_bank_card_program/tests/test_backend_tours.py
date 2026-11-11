from datetime import datetime, time, timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestBackendTours(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.user_admin').write({
            'tz': 'UTC',
            'group_ids': [(4, cls.env.ref('pos_bank_card_program.group_bank_card_finance').id)],
        })
        bank_a = cls.env['res.partner'].create({'name': 'Bank A', 'is_company': True})
        bank_b = cls.env['res.partner'].create({'name': 'Bank B', 'is_company': True})
        program = cls.env['pos.bank.card.program'].create({
            'name': 'Bank A Programme',
            'bank_id': bank_a.id,
            'state': 'active',
            'discount_percent': 10.0,
            'bank_share_percent': 100.0,
            'quota_type': 'count',
            'quota_period': 'month',
            'quota_limit_count': 10,
        })
        cls.env['pos.bank.card.program'].create({
            'name': 'Bank B Programme',
            'bank_id': bank_b.id,
            'state': 'active',
            'discount_percent': 5.0,
        })
        cls.outlet = cls.env['pos.config'].create({'name': 'Outlet 1'})
        cls.env['pos.bank.card.usage'].create({
            'program_id': program.id,
            'config_id': cls.outlet.id,
            'date': fields.Datetime.now().replace(hour=1, minute=0, second=0, microsecond=0),
            'card_bin': '900150',
            'card_last4': '4343',
            'approval_code': 'AP4343',
            'basis_amount': 1000000.0,
            'discount_amount': 100000.0,
            'bank_share_amount': 100000.0,
            'store_share_amount': 0.0,
            'state': 'confirmed',
        })
        last_month = fields.Date.today().replace(day=1) - timedelta(days=1)
        cls.env['pos.bank.card.usage'].create({
            'program_id': program.id,
            'config_id': cls.outlet.id,
            'date': datetime.combine(last_month.replace(day=10), time(10, 0)),
            'card_bin': '900150',
            'card_last4': '4242',
            'approval_code': 'AP1234',
            'basis_amount': 1000000.0,
            'discount_amount': 100000.0,
            'bank_share_amount': 100000.0,
            'store_share_amount': 0.0,
            'state': 'confirmed',
        })

    def test_claim_wizard_empty_and_filled(self):
        self.start_tour(
            '/odoo/action-pos_bank_card_program.action_pos_bank_card_claim_wizard',
            'pos_bank_card_program_claim_wizard_tour', login='admin')

    def test_dashboard(self):
        self.start_tour(
            '/odoo/action-pos_bank_card_program.action_bank_card_dashboard',
            'pos_bank_card_program_dashboard_tour', login='admin')
