from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestBinRange(BankCardCommon):

    def create_range(self, program, value_from, value_to):
        return self.env['pos.bank.card.bin.range'].create({
            'program_id': program.id,
            'bin_from': value_from,
            'bin_to': value_to,
        })

    def test_overlap_same_length(self):
        with self.assertRaises(ValidationError):
            self.create_range(self.program, '900150', '900160')

    def test_overlap_across_lengths(self):
        with self.assertRaises(ValidationError):
            self.create_range(self.program, '90015000', '90015099')

    def test_adjacent_ranges_allowed(self):
        record = self.create_range(self.program, '900300', '900399')
        self.assertTrue(record.id)

    def test_invalid_length(self):
        with self.assertRaises(ValidationError):
            self.create_range(self.program, '90015', '90016')

    def test_mismatched_lengths(self):
        with self.assertRaises(ValidationError):
            self.create_range(self.program, '900150', '90016000')

    def test_reversed_range(self):
        with self.assertRaises(ValidationError):
            self.create_range(self.program, '900160', '900150')

    def test_overlap_allowed_in_another_programme(self):
        other = self.env['pos.bank.card.program'].create({
            'name': 'Other Programme',
            'bank_id': self.bank_b.id,
        })
        record = self.create_range(other, '900150', '900160')
        self.assertTrue(record.id)
