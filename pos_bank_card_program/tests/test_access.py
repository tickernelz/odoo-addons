from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestAccess(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.make_user('bank_card_manager', 'pos_bank_card_program.group_bank_card_manager')
        cls.finance = cls.make_user('bank_card_finance', 'pos_bank_card_program.group_bank_card_finance')
        cls.cashier = cls.make_user('bank_card_cashier', 'point_of_sale.group_pos_user')

    @classmethod
    def make_user(cls, login, group_xmlid):
        group = cls.env.ref(group_xmlid)
        return cls.env['res.users'].create({
            'name': login,
            'login': login,
            'group_ids': [(4, cls.env.ref('base.group_user').id), (4, group.id)],
        })

    def programme_values(self):
        return {'name': 'Access Programme', 'bank_id': self.bank_a.id}

    def test_manager_manages_programmes(self):
        record = self.env['pos.bank.card.program'].with_user(self.manager).create(self.programme_values())
        self.assertTrue(record.id)
        record.with_user(self.manager).write({'name': 'Renamed Programme'})

    def test_finance_reads_but_cannot_create_programme(self):
        found = self.env['pos.bank.card.program'].with_user(self.finance).search([
            ('id', '=', self.program.id)])
        self.assertEqual(found, self.program)
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.program'].with_user(self.finance).create(self.programme_values())

    def test_cashier_reads_but_cannot_create_programme(self):
        found = self.env['pos.bank.card.program'].with_user(self.cashier).search([
            ('id', '=', self.program.id)])
        self.assertEqual(found, self.program)
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.program'].with_user(self.cashier).create(self.programme_values())

    def test_finance_owns_claims(self):
        claim = self.env['pos.bank.card.claim'].with_user(self.finance).create({
            'bank_id': self.bank_a.id,
            'date_from': '2026-01-01',
            'date_to': '2026-01-31',
        })
        self.assertTrue(claim.id)
        self.assertNotEqual(claim.name, '/')
        with self.assertRaises(AccessError):
            self.env['pos.bank.card.claim'].with_user(self.manager).create({
                'bank_id': self.bank_a.id,
                'date_from': '2026-01-01',
                'date_to': '2026-01-31',
            })

    def test_cashier_cannot_write_quota_directly(self):
        program = self.make_programme(
            quota_type='count', quota_period='day', quota_limit_count=5, card_daily_limit=0)
        reserved = self.env['pos.bank.card.program'].reserve_quota(
            program.id, '900550', '5555', 100000.0)
        self.assertTrue(reserved['reserved'])
        quota = self.env['pos.bank.card.quota'].browse(reserved['quota_id'])
        with self.assertRaises(AccessError):
            quota.with_user(self.cashier).write({'used_count': 0})
        usage = self.env['pos.bank.card.usage'].browse(reserved['usage_id'])
        with self.assertRaises(AccessError):
            usage.with_user(self.cashier).write({'discount_amount': 1.0})

    def test_cashier_can_reserve_quota(self):
        result = self.env['pos.bank.card.program'].with_user(self.cashier).reserve_quota(
            self.program.id, '900150', '9999', 1000000.0)
        self.assertTrue(result['reserved'])
