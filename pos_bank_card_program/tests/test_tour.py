from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestBankCardTour(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().write({
            'group_ids': [(4, cls.env.ref('pos_bank_card_program.group_bank_card_manager').id)]})
        cls.bank_a = cls.env['res.partner'].create({'name': 'Bank A', 'is_company': True})
        cls.program = cls.env['pos.bank.card.program'].create({
            'name': 'Bank A Tour Programme',
            'bank_id': cls.bank_a.id,
            'state': 'active',
            'discount_percent': 15.0,
            'min_amount': 500000.0,
            'max_discount': 100000.0,
            'bank_share_percent': 100.0,
            'card_daily_limit': 0,
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
        cls.tour_product = cls.env['product.product'].create({
            'name': 'Outlet Tour Product',
            'type': 'service',
            'list_price': 1000000.0,
            'available_in_pos': True,
            'taxes_id': [(5, 0, 0)],
        })
        bank_journal = cls.env['account.journal'].create({
            'name': 'Tour Bank', 'type': 'bank', 'code': 'TBNK'})
        cls.card_method = cls.env['pos.payment.method'].create({
            'name': 'Card Bank',
            'journal_id': bank_journal.id,
            'bank_card_required': True,
        })
        discount_product = cls.env.ref('pos_bank_card_program.product_bank_card_discount')
        discount_product.product_tmpl_id.sudo().write({'active': True})
        discount_product.sudo().write({'active': True})
        cls.program.discount_product_id = discount_product
        cls.main_pos_config.write({
            'payment_method_ids': [(4, cls.card_method.id)],
        })

    def test_bank_card_cashier_flow(self):
        self.main_pos_config.open_ui()
        self.start_tour(
            '/pos/ui/%d' % self.main_pos_config.id,
            'pos_bank_card_program_tour', login='pos_admin')
        usages = self.env['pos.bank.card.usage'].search([('program_id', '=', self.program.id)])
        self.assertEqual(len(usages), 2)
        confirmed = usages.filtered(lambda usage: usage.state == 'confirmed')
        cancelled = usages.filtered(lambda usage: usage.state == 'cancelled')
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed.discount_amount, 100000.0)
        self.assertEqual(len(cancelled), 1)
