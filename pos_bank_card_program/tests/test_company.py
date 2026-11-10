from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestCompanyScope(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_two = cls.env['res.company'].create({'name': 'Company Two'})
        cls.foreign = cls.env['pos.bank.card.program'].create({
            'name': 'Foreign Programme',
            'bank_id': cls.bank_b.id,
            'company_id': cls.company_two.id,
            'state': 'active',
            'discount_percent': 20.0,
            'card_daily_limit': 0,
        })
        cls.env['pos.bank.card.bin.range'].create({
            'program_id': cls.foreign.id,
            'bin_from': '900900',
            'bin_to': '900999',
            'network': 'Network B',
            'card_type': 'credit',
            'tier': 'Platinum',
            'eligible': True,
        })
        cls.config = cls.env['pos.config'].create({'name': 'Outlet 1'})
        cls.cashier = cls.env['res.users'].create({
            'name': 'bank_card_company_cashier',
            'login': 'bank_card_company_cashier',
            'company_id': cls.env.company.id,
            'company_ids': [(6, 0, [cls.env.company.id])],
            'group_ids': [
                (4, cls.env.ref('base.group_user').id),
                (4, cls.env.ref('point_of_sale.group_pos_user').id),
            ],
        })

    def test_record_rule_hides_foreign_programme(self):
        found = self.env['pos.bank.card.program'].with_user(self.cashier).search([
            ('id', '=', self.foreign.id)])
        self.assertFalse(found)
        own = self.env['pos.bank.card.program'].with_user(self.cashier).search([
            ('id', '=', self.program.id)])
        self.assertEqual(own, self.program)

    def test_pos_load_excludes_foreign_programme(self):
        rows = self.env['pos.bank.card.program']._load_pos_data_search_read({}, self.config)
        loaded_ids = [row['id'] for row in rows]
        self.assertIn(self.program.id, loaded_ids)
        self.assertNotIn(self.foreign.id, loaded_ids)
        range_rows = self.env['pos.bank.card.bin.range']._load_pos_data_search_read({}, self.config)
        self.assertNotIn('900900', [row['bin_from'] for row in range_rows])

    def test_detect_and_reserve_ignore_foreign_programme(self):
        detected = self.env['pos.bank.card.program'].detect_card(
            '900950', '1111', basis_amount=100000.0, config_id=self.config.id)
        self.assertFalse(detected['eligible'])
        self.assertEqual(detected['reason'], 'bin_not_registered')
        reserved = self.env['pos.bank.card.program'].reserve_quota(
            self.foreign.id, '900950', '1111', 100000.0, config_id=self.config.id)
        self.assertFalse(reserved['reserved'])
        self.assertEqual(reserved['reason'], 'bin_not_registered')
