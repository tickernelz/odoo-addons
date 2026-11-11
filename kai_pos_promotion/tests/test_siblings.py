from odoo import Command
from odoo.tests import tagged

from .test_tour import PromoTourBase


@tagged('post_install', '-at_install')
class TestPromoWithSiblingAddons(PromoTourBase):

    def test_promotion_with_manager_pin_and_required_customer(self):
        config = self.main_pos_config
        if 'mv_discount' not in config._fields or 'customer_required_mode' not in config._fields:
            self.skipTest('pos_hr_manager_validation and pos_checkout_extras are not both installed')
        self.env.user.group_ids += self.env.ref('hr.group_hr_user')
        self.env['res.partner'].create({'name': 'Promo Customer'})
        Employee = self.env['hr.employee']
        cashier = Employee.create({'name': 'Promo Cashier', 'company_id': config.company_id.id})
        manager = Employee.create({
            'name': 'Promo Manager', 'company_id': config.company_id.id, 'pin': '5651', 'pos_validation_pin': '482915'})
        second_manager = Employee.create({
            'name': 'Promo Second Manager', 'company_id': config.company_id.id, 'pos_validation_pin': '734261'})
        config.write({
            'module_pos_hr': True,
            'basic_employee_ids': [Command.set((cashier | manager).ids)],
            'mv_employee_ids': [Command.set((manager | second_manager).ids)],
            'mv_discount': True,
            'mv_delete_line': True,
            'mv_price': True,
            'mv_payment': True,
            'customer_required_mode': 'payment',
        })
        config.with_user(self.pos_user).open_ui()
        config.current_session_id.set_opening_control(0, None)
        self.start_tour('/pos/ui/%d' % config.id, 'kai_pos_promotion_siblings_tour', login='pos_user')
        order = self.env['pos.order'].search([('config_id', '=', config.id)])
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.partner_id.name, 'Promo Customer')
        self.assertEqual(order.lines.filtered('kai_promo_id').price_subtotal_incl, -78000)
        self.assertEqual(self.env['pos.manager.validation'].search([]).mapped('action'), ['payment'])

    def test_bank_card_discount_comes_after_the_promotion(self):
        if 'pos.bank.card.program' not in self.env:
            self.skipTest('pos_bank_card_program is not installed')
        self.env.user.group_ids += self.env.ref('pos_bank_card_program.group_bank_card_manager')
        bank = self.env['res.partner'].create({'name': 'Promo Bank', 'is_company': True})
        discount_product = self.env.ref('pos_bank_card_program.product_bank_card_discount')
        discount_product.product_tmpl_id.sudo().write({'active': True})
        program = self.env['pos.bank.card.program'].create({
            'name': 'Promo Bank Card', 'bank_id': bank.id, 'state': 'active', 'discount_percent': 15.0,
            'min_amount': 500000.0, 'max_discount': 100000.0, 'bank_share_percent': 100.0, 'card_daily_limit': 0,
            'discount_product_id': discount_product.id,
        })
        self.env['pos.bank.card.bin.range'].create({
            'program_id': program.id, 'bin_from': '900100', 'bin_to': '900199', 'network': 'Network A',
            'card_type': 'credit', 'tier': 'Platinum', 'eligible': True,
        })
        journal = self.env['account.journal'].create({'name': 'Promo Card Bank', 'type': 'bank', 'code': 'PCBK'})
        method = self.env['pos.payment.method'].create({
            'name': 'Card Bank', 'journal_id': journal.id, 'bank_card_required': True})
        self.main_pos_config.payment_method_ids = [Command.link(method.id)]
        self.main_pos_config.open_ui()
        self.start_tour('/pos/ui/%d' % self.main_pos_config.id, 'kai_pos_promotion_bank_card_tour', login='pos_admin')
        order = self.env['pos.order'].search([('config_id', '=', self.main_pos_config.id)])
        self.assertEqual(order.amount_total, 732000)
        self.assertEqual(order.lines.filtered('kai_promo_id').price_subtotal_incl, -208000)
        usage = self.env['pos.bank.card.usage'].search([('program_id', '=', program.id)])
        self.assertEqual(usage.discount_amount, 100000)
        self.assertAlmostEqual(usage.basis_amount, 832000 / 1.11, places=0)
