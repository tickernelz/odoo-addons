from odoo import Command, fields
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon
from odoo.tests import tagged

from .common import activate, tier_promo


@tagged('post_install', '-at_install')
class PromoTourBase(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax = cls.env['account.tax'].create({
            'name': 'VAT 11% included', 'amount': 11, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'price_include_override': 'tax_included',
        })
        cls.counted = cls.env['product.category'].create({'name': 'Promo Body Care'})
        cls.tickets = cls.env['product.category'].create({'name': 'Promo Tickets'})
        cls.products = {}
        for name, price, category in [
            ('Promo Oil', 185000, cls.counted), ('Promo Wash', 95000, cls.counted), ('Promo Balm', 35000, cls.counted),
            ('Promo Sachet', 5000, cls.counted), ('Promo Gift Box', 520000, cls.counted),
            ('Promo Coffee', 120000, cls.counted), ('Promo Ticket', 250000, cls.tickets),
        ]:
            cls.products[name] = cls.env['product.product'].create({
                'name': name, 'list_price': price, 'available_in_pos': True, 'type': 'consu',
                'categ_id': category.id, 'taxes_id': [Command.set(cls.tax.ids)],
            })
        cls.main_pos_config.kai_timezone = 'Asia/Makassar'
        cls.seminyak = activate(tier_promo(cls.env, 'Seminyak Tier', [
            (300000, 'percent', 10), (500000, 'percent', 15), (1000000, 'percent', 20)],
            excluded_categ_ids=[Command.set(cls.tickets.ids)]))

    def _orders(self):
        return self.env['pos.order'].search([('config_id', '=', self.main_pos_config.id)], order='id')

    def _promo_lines(self, order):
        return order.lines.filtered('kai_promo_id')


@tagged('post_install', '-at_install')
class TestPromoTours(PromoTourBase):

    def test_engine_rules(self):
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_engine_tour', login='pos_admin')

    def test_tiers_banner_receipt_and_refund(self):
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_tier_tour', login='pos_admin')
        first, second, refund = self._orders()
        line = self._promo_lines(first)
        self.assertEqual(line.price_subtotal_incl, -31500)
        self.assertEqual(line.full_product_name, 'Seminyak Tier – 10%')
        self.assertEqual(line.tax_ids, self.tax)
        self.assertEqual(first.amount_total, 283500)
        self.assertEqual(self._promo_lines(second).price_subtotal_incl, -208000)
        self.assertEqual(second.amount_total, 1082000)
        returned = self._promo_lines(refund)
        self.assertEqual(returned.kai_promo_refund_of_id, self._promo_lines(second))
        self.assertEqual(returned.kai_promo_id, self.seminyak)
        product_line = refund.lines - returned
        self.assertEqual(abs(returned.price_subtotal_incl), 104000)
        self.assertLess(returned.price_subtotal_incl * product_line.price_subtotal_incl, 0)
        self.assertEqual(refund.amount_total, -416000)

    def test_exclusive_group_with_a_native_programme(self):
        activate(tier_promo(self.env, 'National Tier', [(400000, 'percent', 12), (800000, 'amount', 120000)], priority=20))
        self.env['loyalty.program'].create({
            'name': 'Native Order 25%', 'program_type': 'promotion', 'trigger': 'auto', 'applies_on': 'current',
            'pos_ok': True, 'kai_exclusive_group': 'order-tier',
            'rule_ids': [Command.create({'reward_point_mode': 'order', 'reward_point_amount': 1,
                                         'minimum_amount': 900000, 'minimum_amount_tax_mode': 'incl'})],
            'reward_ids': [Command.create({'reward_type': 'discount', 'discount': 25, 'discount_mode': 'percent',
                                           'discount_applicability': 'order', 'required_points': 1})],
        })
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_group_tour', login='pos_admin')
        order = self._orders()
        self.assertEqual(len(order), 1)
        self.assertEqual(self._promo_lines(order).kai_promo_id, self.seminyak)
        self.assertFalse(order.lines.filtered('is_reward_line'))

    def test_happy_hour_on_a_native_programme(self):
        coffee = self.products['Promo Coffee']
        self.env['loyalty.program'].create({
            'name': 'Coffee Happy Hour', 'program_type': 'promotion', 'trigger': 'auto', 'applies_on': 'current',
            'pos_ok': True, 'kai_happy_hour': True,
            'kai_schedule_ids': [Command.create({'hour_from': 14, 'hour_to': 16})],
            'rule_ids': [Command.create({'reward_point_mode': 'order', 'reward_point_amount': 1,
                                         'product_ids': [Command.set(coffee.ids)]})],
            'reward_ids': [Command.create({'reward_type': 'discount', 'discount': 15, 'discount_mode': 'percent',
                                           'discount_applicability': 'specific', 'required_points': 1,
                                           'discount_product_ids': [Command.set(coffee.ids)]})],
        })
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_happy_hour_tour', login='pos_admin')
        order = self._orders()
        self.assertEqual(order.amount_total, 240000)
        self.assertFalse(order.lines.filtered('is_reward_line'))

    def test_offline_orders_keep_their_promotion(self):
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_offline_tour', login='pos_admin')
        offline, online = self._orders()
        self.assertEqual(self._promo_lines(offline).price_subtotal_incl, -208000)
        self.assertEqual(offline.amount_total, 832000)
        self.assertFalse(self._promo_lines(online))
        self.assertEqual(fields.Date.today(), offline.date_order.date())
