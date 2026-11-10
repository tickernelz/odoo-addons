import psycopg2

from odoo import api
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestQuota(BankCardCommon):

    def reserve(self, program, card_bin, last4, basis):
        return self.env['pos.bank.card.program'].reserve_quota(program.id, card_bin, last4, basis)

    def test_second_reservation_gets_quota_exhausted(self):
        program = self.make_programme(
            quota_type='count', quota_period='day', quota_limit_count=1, card_daily_limit=0)
        self.assertTrue(self.reserve(program, '900550', '1111', 100000.0)['reserved'])
        result = self.reserve(program, '900550', '2222', 100000.0)
        self.assertFalse(result['reserved'])
        self.assertEqual(result['reason'], 'quota_exhausted')
        usages = self.env['pos.bank.card.usage'].search([('program_id', '=', program.id)])
        self.assertEqual(len(usages), 1)

    def test_amount_quota(self):
        program = self.make_programme(
            quota_type='amount', quota_period='day', quota_limit_amount=100000.0,
            card_daily_limit=0, discount_percent=60.0)
        self.assertTrue(self.reserve(program, '900550', '1111', 100000.0)['reserved'])
        result = self.reserve(program, '900550', '2222', 100000.0)
        self.assertFalse(result['reserved'])
        self.assertEqual(result['reason'], 'quota_exhausted')

    def test_release_returns_quota(self):
        program = self.make_programme(
            quota_type='count', quota_period='day', quota_limit_count=1, card_daily_limit=0)
        reserved = self.reserve(program, '900550', '1111', 100000.0)
        self.assertTrue(reserved['reserved'])
        released = self.env['pos.bank.card.program'].release_quota(reserved['usage_id'])
        self.assertTrue(released['released'])
        usage = self.env['pos.bank.card.usage'].browse(reserved['usage_id'])
        self.assertEqual(usage.state, 'cancelled')
        self.assertTrue(self.reserve(program, '900550', '2222', 100000.0)['reserved'])

    def test_confirm_reservation(self):
        reserved = self.reserve(self.program, '900150', '3333', 1000000.0)
        self.assertTrue(reserved['reserved'])
        confirmed = self.env['pos.bank.card.program'].confirm_quota(reserved['usage_id'])
        self.assertTrue(confirmed['confirmed'])
        usage = self.env['pos.bank.card.usage'].browse(reserved['usage_id'])
        self.assertEqual(usage.state, 'confirmed')
        self.assertEqual(usage.masked_card, '900150 **** 3333')
        released = self.env['pos.bank.card.program'].release_quota(reserved['usage_id'])
        self.assertFalse(released['released'])

    def test_card_daily_limit_on_reserve(self):
        self.assertTrue(self.reserve(self.program, '900150', '4444', 1000000.0)['reserved'])
        result = self.reserve(self.program, '900150', '4444', 1000000.0)
        self.assertFalse(result['reserved'])
        self.assertEqual(result['reason'], 'card_used_today')

    def test_reserve_rejects_unregistered_bin(self):
        result = self.reserve(self.program, '123456', '1111', 1000000.0)
        self.assertFalse(result['reserved'])
        self.assertEqual(result['reason'], 'bin_not_registered')

    def test_reserve_rejects_excluded_tier(self):
        result = self.reserve(self.program, '900250', '1111', 1000000.0)
        self.assertFalse(result['reserved'])
        self.assertEqual(result['reason'], 'tier_excluded')

    def make_committed_programme(self):
        cursor = self.env.registry.cursor()
        try:
            env = api.Environment(cursor, self.env.user.id, {})
            bank = env['res.partner'].create({'name': 'Bank C', 'is_company': True})
            program = env['pos.bank.card.program'].create({
                'name': 'Concurrent Programme',
                'bank_id': bank.id,
                'state': 'active',
                'discount_percent': 10.0,
                'quota_type': 'count',
                'quota_period': 'day',
                'quota_limit_count': 1,
                'card_daily_limit': 0,
            })
            env['pos.bank.card.bin.range'].create({
                'program_id': program.id,
                'bin_from': '900800',
                'bin_to': '900899',
                'network': 'Network C',
                'card_type': 'credit',
                'tier': 'Platinum',
                'eligible': True,
            })
            cursor.commit()
            return bank.id, program.id
        finally:
            cursor.close()

    def drop_committed_programme(self, bank_id, program_id):
        cursor = self.env.registry.cursor()
        try:
            env = api.Environment(cursor, self.env.user.id, {})
            env['pos.bank.card.usage'].search([('program_id', '=', program_id)]).unlink()
            env['pos.bank.card.program'].browse(program_id).unlink()
            env['res.partner'].browse(bank_id).unlink()
            cursor.commit()
        finally:
            cursor.close()

    def count_committed_usages(self, program_id):
        cursor = self.env.registry.cursor()
        try:
            env = api.Environment(cursor, self.env.user.id, {})
            return env['pos.bank.card.usage'].search_count([('program_id', '=', program_id)])
        finally:
            cursor.close()

    def test_concurrent_reservations_two_cursors(self):
        bank_id, program_id = self.make_committed_programme()
        cursor_a = self.env.registry.cursor()
        cursor_b = self.env.registry.cursor()
        cursor_c = self.env.registry.cursor()
        try:
            env_a = api.Environment(cursor_a, self.env.user.id, {})
            first = env_a['pos.bank.card.program'].reserve_quota(
                program_id, '900850', '1111', 100000.0)
            self.assertTrue(first['reserved'])
            env_b = api.Environment(cursor_b, self.env.user.id, {})
            cursor_b.execute("SET LOCAL lock_timeout = '2s'")
            with self.assertRaises(psycopg2.errors.LockNotAvailable):
                env_b['pos.bank.card.program'].reserve_quota(
                    program_id, '900850', '2222', 100000.0)
            cursor_b.rollback()
            cursor_a.commit()
            env_c = api.Environment(cursor_c, self.env.user.id, {})
            second = env_c['pos.bank.card.program'].reserve_quota(
                program_id, '900850', '2222', 100000.0)
            cursor_c.commit()
            self.assertFalse(second['reserved'])
            self.assertEqual(second['reason'], 'quota_exhausted')
            self.assertEqual(self.count_committed_usages(program_id), 1)
        finally:
            cursor_a.close()
            cursor_b.close()
            cursor_c.close()
            self.drop_committed_programme(bank_id, program_id)
