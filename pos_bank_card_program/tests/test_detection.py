from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestDetection(BankCardCommon):

    def detect(self, *args, **kwargs):
        return self.env['pos.bank.card.program'].detect_card(*args, **kwargs)

    def test_eligible_acceptance_scenario(self):
        result = self.detect('900150', '1234', basis_amount=1000000.0)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['program_id'], self.program.id)
        self.assertEqual(result['card_tier'], 'Platinum')
        self.assertEqual(result['discount_amount'], 100000.0)
        self.assertEqual(result['bank_share_amount'], 100000.0)
        self.assertEqual(result['store_share_amount'], 0.0)

    def test_tier_excluded(self):
        result = self.detect('900250', '1234', basis_amount=1000000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'tier_excluded')
        self.assertEqual(result['message'], 'The Gold tier is not included.')

    def test_bin_not_registered(self):
        result = self.detect('123456', '1234', basis_amount=1000000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'bin_not_registered')

    def test_below_minimum(self):
        result = self.detect('900150', '1234', basis_amount=100000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'below_minimum')

    def test_offline_refused_by_default(self):
        result = self.detect('900150', '1234', basis_amount=1000000.0, online=False)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'offline')

    def test_offline_allowed_when_programme_allows(self):
        self.program.offline_allowed = True
        result = self.detect('900150', '1234', basis_amount=1000000.0, online=False)
        self.assertTrue(result['eligible'])

    def test_card_used_today(self):
        reserved = self.env['pos.bank.card.program'].reserve_quota(
            self.program.id, '900150', '7777', 1000000.0)
        self.assertTrue(reserved['reserved'])
        result = self.detect('900150', '7777', basis_amount=1000000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'card_used_today')

    def test_quota_exhausted(self):
        program = self.make_programme(
            quota_type='count', quota_period='day', quota_limit_count=1, card_daily_limit=0)
        reserved = self.env['pos.bank.card.program'].reserve_quota(
            program.id, '900550', '1111', 100000.0)
        self.assertTrue(reserved['reserved'])
        result = self.detect('900550', '2222', basis_amount=100000.0)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'quota_exhausted')

    def test_discount_basis_percent_and_cap(self):
        amounts = self.program_half.compute_discount(200000.0)
        self.assertEqual(amounts['discount_amount'], 20000.0)
        amounts = self.program_half.compute_discount(1000000.0)
        self.assertEqual(amounts['discount_amount'], 75000.0)

    def test_discount_half_share(self):
        amounts = self.program_half.compute_discount(200000.0)
        self.assertEqual(amounts['bank_share_amount'], 10000.0)
        self.assertEqual(amounts['store_share_amount'], 10000.0)

    def test_eight_digit_bin_prefix_match(self):
        self.env['pos.bank.card.bin.range'].create({
            'program_id': self.program_half.id,
            'bin_from': '90070000',
            'bin_to': '90070099',
            'network': 'Network B',
            'card_type': 'debit',
            'tier': 'Platinum',
            'eligible': True,
        })
        result = self.detect('90070042', '1234', basis_amount=200000.0)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['program_id'], self.program_half.id)
