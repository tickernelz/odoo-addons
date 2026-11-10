from odoo.tests import TransactionCase


class BankCardCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank_a = cls.env['res.partner'].create({'name': 'Bank A', 'is_company': True})
        cls.bank_b = cls.env['res.partner'].create({'name': 'Bank B', 'is_company': True})
        cls.program = cls.env['pos.bank.card.program'].create({
            'name': 'Bank A Programme',
            'bank_id': cls.bank_a.id,
            'state': 'active',
            'discount_percent': 15.0,
            'min_amount': 500000.0,
            'max_discount': 100000.0,
            'bank_share_percent': 100.0,
            'card_daily_limit': 1,
        })
        cls.env['pos.bank.card.bin.range'].create({
            'program_id': cls.program.id,
            'bin_from': '900100',
            'bin_to': '900199',
            'network': 'Network A',
            'card_type': 'credit',
            'tier': 'Platinum',
            'eligible': True,
        })
        cls.env['pos.bank.card.bin.range'].create({
            'program_id': cls.program.id,
            'bin_from': '900200',
            'bin_to': '900299',
            'network': 'Network A',
            'card_type': 'credit',
            'tier': 'Gold',
            'eligible': False,
            'ineligible_reason': 'The Gold tier is not included.',
        })
        cls.program_half = cls.env['pos.bank.card.program'].create({
            'name': 'Bank B Programme',
            'bank_id': cls.bank_b.id,
            'state': 'active',
            'discount_percent': 10.0,
            'max_discount': 75000.0,
            'bank_share_percent': 50.0,
            'card_daily_limit': 0,
        })
        cls.env['pos.bank.card.bin.range'].create({
            'program_id': cls.program_half.id,
            'bin_from': '900300',
            'bin_to': '900399',
            'network': 'Network B',
            'card_type': 'credit',
            'tier': 'Signature',
            'eligible': True,
        })

    def make_programme(self, **overrides):
        values = {
            'name': 'Extra Programme',
            'bank_id': self.bank_a.id,
            'state': 'active',
            'discount_percent': 10.0,
        }
        values.update(overrides)
        program = self.env['pos.bank.card.program'].create(values)
        self.env['pos.bank.card.bin.range'].create({
            'program_id': program.id,
            'bin_from': '900500',
            'bin_to': '900599',
            'network': 'Network A',
            'card_type': 'credit',
            'tier': 'Platinum',
            'eligible': True,
        })
        return program
