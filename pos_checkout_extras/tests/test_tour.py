from odoo import fields
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class CheckoutExtrasTourCase(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Ayu Lestari'})
        cls.tour_product = cls.env['product.product'].create({
            'name': 'Checkout Tour Product',
            'type': 'service',
            'list_price': 100.0,
            'available_in_pos': True,
            'taxes_id': [(5, 0, 0)],
        })

    def run_tour(self, tour):
        self.main_pos_config.with_user(self.pos_admin).open_ui()
        self.start_pos_tour(tour, login='pos_admin')
        return self.env['pos.order'].search(
            [('session_id', '=', self.main_pos_config.current_session_id.id)], order='id desc', limit=1)


@tagged('post_install', '-at_install')
class TestCheckoutExtrasTour(CheckoutExtrasTourCase):

    def test_order_mode_asks_for_the_customer_before_payment(self):
        self.main_pos_config.customer_required_mode = 'order'
        order = self.run_tour('pos_checkout_extras_order_mode')
        self.assertEqual(order.partner_id, self.customer)
        self.assertEqual(order.state, 'paid')

    def test_payment_mode_asks_for_the_customer_at_validation(self):
        self.main_pos_config.customer_required_mode = 'payment'
        order = self.run_tour('pos_checkout_extras_payment_mode')
        self.assertEqual(order.partner_id, self.customer)

    def test_fast_payment_asks_for_the_customer(self):
        self.main_pos_config.write({
            'customer_required_mode': 'order',
            'use_fast_payment': True,
            'fast_payment_method_ids': [(6, 0, self.bank_payment_method.ids)],
        })
        order = self.run_tour('pos_checkout_extras_fast_payment')
        self.assertEqual(order.partner_id, self.customer)

    def test_payment_reference_reaches_receipt_order_and_payments(self):
        self.main_pos_config.allow_payment_reference = True
        order = self.run_tour('pos_checkout_extras_payment_reference')
        self.assertEqual(order.payment_reference, 'TRF-2026-0001')
        self.assertEqual(order.payment_ids.mapped('payment_reference'), ['TRF-2026-0001'])

    def test_refund_without_customer_is_not_blocked(self):
        self.main_pos_config.with_user(self.pos_admin).open_ui()
        session = self.main_pos_config.current_session_id
        self.env['pos.order'].sync_from_ui([{
            'name': 'Refundable Order',
            'pos_reference': 'Refundable Order',
            'uuid': '00000-000-0001',
            'session_id': session.id,
            'pricelist_id': self.main_pos_config.pricelist_id.id,
            'date_order': fields.Datetime.to_string(fields.Datetime.now()),
            'amount_paid': 100.0,
            'amount_return': 0.0,
            'amount_tax': 0.0,
            'amount_total': 100.0,
            'lines': [(0, 0, {
                'product_id': self.tour_product.id,
                'qty': 1,
                'price_unit': 100.0,
                'price_subtotal': 100.0,
                'price_subtotal_incl': 100.0,
                'tax_ids': [],
            })],
            'payment_ids': [(0, 0, {
                'amount': 100.0,
                'payment_method_id': self.bank_payment_method.id,
                'name': fields.Datetime.now(),
            })],
            'user_id': self.pos_admin.id,
            'last_order_preparation_change': '{}',
        }])
        self.main_pos_config.customer_required_mode = 'payment'
        self.start_pos_tour('pos_checkout_extras_refund', login='pos_admin')
        refund = self.env['pos.order'].search([('session_id', '=', session.id), ('is_refund', '=', True)])
        self.assertEqual(len(refund), 1)
        self.assertFalse(refund.partner_id)
        self.assertEqual(refund.state, 'paid')


@tagged('post_install', '-at_install')
class TestCheckoutExtrasWithBankCard(CheckoutExtrasTourCase):

    def setUp(self):
        super().setUp()
        if 'pos.bank.card.program' not in self.env:
            self.skipTest('pos_bank_card_program is not installed in this database')
        self.env.user.sudo().write({
            'group_ids': [(4, self.env.ref('pos_bank_card_program.group_bank_card_manager').id)]})
        bank = self.env['res.partner'].create({'name': 'Bank A', 'is_company': True})
        program = self.env['pos.bank.card.program'].create({
            'name': 'Bank A Checkout Programme',
            'bank_id': bank.id,
            'state': 'active',
            'discount_percent': 10.0,
            'min_amount': 0.0,
            'max_discount': 0.0,
            'bank_share_percent': 100.0,
            'card_daily_limit': 0,
        })
        self.env['pos.bank.card.bin.range'].create({
            'program_id': program.id,
            'bin_from': '900100',
            'bin_to': '900199',
            'network': 'Network A',
            'card_type': 'credit',
            'tier': 'Platinum',
            'eligible': True,
        })
        discount_product = self.env.ref('pos_bank_card_program.product_bank_card_discount')
        discount_product.product_tmpl_id.sudo().write({'active': True})
        discount_product.sudo().write({'active': True})
        program.discount_product_id = discount_product
        card_journal = self.env['account.journal'].create({'name': 'Checkout Card', 'type': 'bank', 'code': 'CXCB'})
        card_method = self.env['pos.payment.method'].create({
            'name': 'Card Bank',
            'journal_id': card_journal.id,
            'bank_card_required': True,
        })
        self.main_pos_config.write({
            'payment_method_ids': [(4, card_method.id)],
            'customer_required_mode': 'order',
            'allow_payment_reference': True,
        })

    def test_customer_dialog_comes_before_the_card_and_receipt_keeps_both_blocks(self):
        order = self.run_tour('pos_checkout_extras_with_bank_card')
        self.assertEqual(order.partner_id, self.customer)
        self.assertEqual(order.payment_reference, 'EDC-778899')
        self.assertTrue(order.bank_card_usage_id)
