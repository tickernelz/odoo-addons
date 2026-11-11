import datetime
from unittest import mock

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestFixedDiscount(BankCardCommon):

    def make_fixed(self, **overrides):
        values = {
            'discount_type': 'fixed',
            'discount_fixed_amount': 1000.0,
            'card_daily_limit': 0,
        }
        values.update(overrides)
        return self.make_programme(**values)

    def reserve(self, program, last4='1111', basis=100000.0):
        return self.env['pos.bank.card.program'].reserve_quota(
            program.id, '900550', last4, basis)

    def test_fixed_below_basis_and_share_split(self):
        program = self.make_fixed(bank_share_percent=50.0)
        amounts = program.compute_discount(100000.0)
        self.assertEqual(amounts['discount_amount'], 1000.0)
        self.assertEqual(amounts['bank_share_amount'], 500.0)
        self.assertEqual(amounts['store_share_amount'], 500.0)

    def test_fixed_equal_to_and_above_basis(self):
        program = self.make_fixed()
        self.assertEqual(program.compute_discount(1000.0)['discount_amount'], 1000.0)
        self.assertEqual(program.compute_discount(400.0)['discount_amount'], 400.0)
        self.assertEqual(program.compute_discount(0.0)['discount_amount'], 0.0)

    def test_fixed_ignores_max_discount(self):
        program = self.make_fixed(max_discount=10.0)
        self.assertEqual(program.compute_discount(100000.0)['discount_amount'], 1000.0)

    def test_fixed_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.make_fixed(discount_fixed_amount=0.0)

    def test_percent_bounds_do_not_apply_to_fixed(self):
        program = self.make_fixed(discount_percent=150.0)
        self.assertEqual(program.compute_discount(100000.0)['discount_amount'], 1000.0)
        with self.assertRaises(ValidationError):
            self.make_programme(discount_percent=150.0)

    def test_programme_without_type_stays_percent(self):
        self.assertEqual(self.program.discount_type, 'percent')
        self.assertEqual(self.program.compute_discount(1000000.0)['discount_amount'], 100000.0)
        self.assertEqual(self.program.compute_discount(200000.0)['discount_amount'], 30000.0)

    def test_fixed_rounding_two_decimals(self):
        program = self.make_fixed(discount_fixed_amount=1000.456)
        self.assertEqual(program.currency_id.decimal_places, 2)
        self.assertEqual(program.compute_discount(5000.0)['discount_amount'], 1000.46)

    def test_fixed_rounding_zero_decimals(self):
        currency = self.env['res.currency'].create({
            'name': 'TST', 'symbol': 'T', 'rounding': 1.0, 'decimal_places': 0})
        company = self.env['res.company'].create({
            'name': 'Zero Decimal Company', 'currency_id': currency.id})
        program = self.env['pos.bank.card.program'].create({
            'name': 'Zero Decimal Programme',
            'bank_id': self.bank_a.id,
            'company_id': company.id,
            'state': 'active',
            'discount_type': 'fixed',
            'discount_fixed_amount': 1000.4,
            'card_daily_limit': 0,
        })
        self.assertEqual(program.compute_discount(5000.0)['discount_amount'], 1000.0)
        self.assertEqual(program.compute_discount(500.4)['discount_amount'], 500.0)

    def test_fixed_below_minimum_rejected(self):
        program = self.make_fixed(min_amount=5000.0)
        result = self.env['pos.bank.card.program'].detect_card(
            '900550', '1111', basis_amount=4000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'below_minimum')
        result = self.env['pos.bank.card.program'].detect_card(
            '900550', '1111', basis_amount=5000.0)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['program_id'], program.id)
        self.assertEqual(result['discount_amount'], 1000.0)

    def test_fixed_monthly_quota_fifty_then_next_month(self):
        program = self.make_fixed(
            quota_type='count', quota_period='month', quota_limit_count=50)
        day = fields.Date.context_today(self.program)
        quota = program._quota_for(day, create=True)
        quota.sudo().write({'used_count': 49})
        self.assertTrue(self.reserve(program)['reserved'])
        quota.invalidate_recordset(['used_count'])
        self.assertEqual(quota.used_count, 50)
        refused = self.reserve(program, last4='2222')
        self.assertFalse(refused['reserved'])
        self.assertEqual(refused['reason'], 'quota_exhausted')
        next_month = (day.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        with mock.patch.object(
                fields.Date, 'context_today',
                lambda record=None, timestamp=None: next_month):
            self.assertTrue(self.reserve(program, last4='3333')['reserved'])

    def test_fixed_refund_returns_quota(self):
        program = self.make_fixed(
            quota_type='amount', quota_period='day', quota_limit_amount=100000.0,
            refund_returns_quota=True)
        reserved = self.reserve(program)
        self.assertTrue(reserved['reserved'])
        usage = self.env['pos.bank.card.usage'].browse(reserved['usage_id'])
        self.assertEqual(usage.discount_amount, 1000.0)
        quota = usage.quota_id
        self.assertEqual(quota.used_count, 1)
        self.assertEqual(quota.used_amount, 1000.0)
        usage.cancel_for_refund()
        self.assertEqual(usage.state, 'cancelled')
        quota.invalidate_recordset(['used_count', 'used_amount'])
        self.assertEqual(quota.used_count, 0)
        self.assertEqual(quota.used_amount, 0.0)
