from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPosBankCardFlow(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().write({
            'group_ids': [(4, cls.env.ref('pos_bank_card_program.group_bank_card_manager').id)]})
        cls.config = cls.basic_config
        cls.bank_a = cls.env['res.partner'].create({'name': 'Bank A', 'is_company': True})
        cls.bank_b = cls.env['res.partner'].create({'name': 'Bank B', 'is_company': True})
        cls.discount_account = cls.env['account.account'].create({
            'name': 'Bank Card Programme Discount', 'code': 'BC6000', 'account_type': 'expense'})
        cls.claim_account = cls.env['account.account'].create({
            'name': 'Bank Claim Receivable', 'code': 'BC1200', 'account_type': 'asset_receivable',
            'reconcile': True})
        cls.reclass_journal = cls.env['account.journal'].create({
            'name': 'Bank Card Reclassification', 'code': 'BCR', 'type': 'general'})
        cls.program = cls.env['pos.bank.card.program'].create({
            'name': 'Bank A Programme',
            'bank_id': cls.bank_a.id,
            'company_id': cls.company.id,
            'state': 'active',
            'discount_percent': 15.0,
            'min_amount': 500000.0,
            'max_discount': 100000.0,
            'bank_share_percent': 100.0,
            'quota_type': 'count',
            'quota_period': 'day',
            'quota_limit_count': 3,
            'card_daily_limit': 1,
            'discount_account_id': cls.discount_account.id,
            'receivable_account_id': cls.claim_account.id,
            'journal_id': cls.reclass_journal.id,
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
        cls.program_half = cls.env['pos.bank.card.program'].create({
            'name': 'Bank B Programme',
            'bank_id': cls.bank_b.id,
            'company_id': cls.company.id,
            'state': 'active',
            'discount_percent': 10.0,
            'max_discount': 75000.0,
            'bank_share_percent': 50.0,
            'card_daily_limit': 0,
            'discount_account_id': cls.discount_account.id,
            'receivable_account_id': cls.claim_account.id,
            'journal_id': cls.reclass_journal.id,
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
        cls.product = cls.env['product.product'].create({
            'name': 'Outlet Product 1',
            'type': 'service',
            'list_price': 1000000.0,
            'available_in_pos': True,
            'taxes_id': [(5, 0, 0)],
        })
        cls.discount_product = cls.env.ref('pos_bank_card_program.product_bank_card_discount')
        cls.bank_pm1.write({'bank_card_required': True})

    def reserve(self, program, card_bin, last4, basis, approval='AP1234'):
        result = self.env['pos.bank.card.program'].reserve_quota(
            program.id, card_bin, last4, basis, approval_code=approval,
            config_id=self.config.id, payment_method_id=self.bank_pm1.id)
        self.assertTrue(result['reserved'], result)
        return self.env['pos.bank.card.usage'].browse(result['usage_id'])

    def make_order(self, program, discount, usage_ref='', offline=False, approval='AP1234',
                   card_bin='900150', last4='1234'):
        order = self.env['pos.order'].create({
            'session_id': self.pos_session.id,
            'company_id': self.company.id,
            'amount_tax': 0.0,
            'amount_total': 1000000.0 - discount,
            'amount_paid': 1000000.0 - discount,
            'amount_return': 0.0,
            'bank_card_program_id': program.id,
            'bank_card_masked': '9001 50** **** 1234',
            'bank_card_reservation_state': 'reserved',
            'bank_card_usage_ref': usage_ref,
            'bank_card_offline': offline,
            'lines': [
                (0, 0, {
                    'product_id': self.product.id,
                    'price_unit': 1000000.0,
                    'price_subtotal': 1000000.0,
                    'price_subtotal_incl': 1000000.0,
                    'qty': 1,
                    'tax_ids': [(5, 0, 0)],
                    'full_product_name': self.product.name,
                }),
                (0, 0, {
                    'product_id': self.discount_product.id,
                    'price_unit': -discount,
                    'price_subtotal': -discount,
                    'price_subtotal_incl': -discount,
                    'qty': 1,
                    'tax_ids': [(5, 0, 0)],
                    'is_bank_card_discount': True,
                    'full_product_name': self.discount_product.name,
                }),
            ],
            'payment_ids': [
                (0, 0, {
                    'payment_method_id': self.bank_pm1.id,
                    'amount': 1000000.0 - discount,
                    'card_bin': card_bin,
                    'card_no': last4,
                    'card_tier': 'Platinum',
                    'payment_method_authcode': approval,
                    'bank_card_program_id': program.id,
                    'bank_card_discount': discount,
                }),
            ],
        })
        return order

    def test_paid_order_confirms_usage(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        self.assertEqual(usage.discount_amount, 100000.0)
        order = self.make_order(self.program, 100000.0, usage_ref=str(usage.id))
        order.action_pos_order_paid()
        self.assertEqual(usage.state, 'confirmed')
        self.assertEqual(usage.pos_order_id, order)
        self.assertEqual(usage.payment_id, order.payment_ids)
        self.assertEqual(order.bank_card_usage_id, usage)
        self.assertEqual(order.bank_card_reservation_state, 'confirmed')

    def test_validation_guard_without_usage(self):
        self.open_new_session()
        order = self.make_order(self.program, 100000.0)
        with self.assertRaises(UserError) as caught:
            order.action_pos_order_paid()
        self.assertIn('usage record', str(caught.exception))

    def test_validation_guard_with_cancelled_usage(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        self.env['pos.bank.card.program'].release_quota(usage.id, config_id=self.config.id)
        order = self.make_order(self.program, 100000.0, usage_ref=str(usage.id))
        with self.assertRaises(UserError) as caught:
            order.action_pos_order_paid()
        self.assertIn('cancelled', str(caught.exception))

    def test_validation_guard_without_approval_code(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0, approval=False)
        order = self.make_order(
            self.program, 100000.0, usage_ref=str(usage.id), approval=False)
        with self.assertRaises(UserError) as caught:
            order.action_pos_order_paid()
        self.assertIn('Approval Code', str(caught.exception))

    def test_offline_discount_creates_usage_when_synced(self):
        self.program_half.write({'offline_allowed': True, 'offline_allowance': 2})
        self.open_new_session()
        order = self.make_order(self.program_half, 75000.0, offline=True,
                                card_bin='900350', last4='9876')
        order.action_pos_order_paid()
        usage = order.bank_card_usage_id
        self.assertTrue(usage)
        self.assertEqual(usage.state, 'confirmed')
        self.assertTrue(usage.offline)
        self.assertEqual(usage.discount_amount, 75000.0)
        self.assertEqual(usage.bank_share_amount, 37500.0)
        self.assertFalse(usage.needs_review)
        self.assertEqual(usage.claim_state, 'not_claimed')

    def test_offline_discount_without_permission_is_not_claimable(self):
        self.open_new_session()
        order = self.make_order(self.program_half, 75000.0, offline=True,
                                card_bin='900350', last4='9876')
        order.action_pos_order_paid()
        self.assertEqual(order.state, 'paid')
        usage = order.bank_card_usage_id
        self.assertTrue(usage)
        self.assertTrue(usage.offline)
        self.assertTrue(usage.needs_review)
        self.assertEqual(usage.claim_state, 'cancelled')
        self.assertEqual(usage.bank_share_amount, 0.0)
        self.assertEqual(usage.store_share_amount, 75000.0)

    def test_offline_allowance_is_enforced_by_the_server(self):
        self.program_half.write({'offline_allowed': True, 'offline_allowance': 1})
        self.open_new_session()
        first = self.make_order(self.program_half, 75000.0, offline=True,
                                card_bin='900350', last4='9876')
        first.action_pos_order_paid()
        self.assertFalse(first.bank_card_usage_id.needs_review)
        self.assertEqual(first.bank_card_usage_id.bank_share_amount, 37500.0)
        second = self.make_order(self.program_half, 75000.0, offline=True,
                                 card_bin='900350', last4='9876')
        second.action_pos_order_paid()
        self.assertEqual(second.state, 'paid')
        self.assertTrue(second.bank_card_usage_id.needs_review)
        self.assertEqual(second.bank_card_usage_id.bank_share_amount, 0.0)

    def test_offline_order_syncs_from_ui_without_bank_claim(self):
        self.open_new_session()
        data = self.create_ui_order_data(
            [
                (self.product, 1),
                {
                    'product': self.discount_product,
                    'quantity': 1,
                    'price_unit': -75000.0,
                    'price_subtotal': -75000.0,
                    'price_subtotal_incl': -75000.0,
                    'is_bank_card_discount': True,
                },
            ],
            pos_order_ui_args={
                'bank_card_program_id': self.program_half.id,
                'bank_card_masked': '9003 50** **** 9876',
                'bank_card_reservation_state': 'reserved',
                'bank_card_offline': True,
            },
            payments=[(self.bank_pm1, 925000.0)],
        )
        data['payment_ids'][0][2].update({
            'card_bin': '900350',
            'card_no': '9876',
            'card_tier': 'Signature',
            'payment_method_authcode': 'AP1234',
            'bank_card_program_id': self.program_half.id,
            'bank_card_discount': 75000.0,
            'bank_card_bank_share': 37500.0,
        })
        result = self.env['pos.order'].sync_from_ui([data])
        order = self.env['pos.order'].browse(result['pos.order'][0]['id'])
        self.assertEqual(order.state, 'paid')
        usage = order.bank_card_usage_id
        self.assertTrue(usage)
        self.assertTrue(usage.needs_review)
        self.assertEqual(usage.claim_state, 'cancelled')
        self.assertEqual(usage.bank_share_amount, 0.0)

    def test_discount_without_card_payment_is_rejected(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        order = self.make_order(self.program, 100000.0, usage_ref=str(usage.id))
        order.payment_ids.write({'bank_card_program_id': False})
        with self.assertRaises(UserError) as caught:
            order.action_pos_order_paid()
        self.assertIn('not paid with', str(caught.exception))

    def test_discount_amount_mismatch_is_rejected(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        order = self.make_order(self.program, 50000.0, usage_ref=str(usage.id))
        with self.assertRaises(UserError) as caught:
            order.action_pos_order_paid()
        self.assertIn('do not match', str(caught.exception))

    def test_refund_cancels_usage_and_keeps_quota_by_default(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        quota = usage.quota_id
        order = self.make_order(self.program, 100000.0, usage_ref=str(usage.id))
        order.action_pos_order_paid()
        product_line = order.lines.filtered(lambda line: not line.is_bank_card_discount)
        refund = self.env['pos.order'].create({
            'session_id': self.pos_session.id,
            'company_id': self.company.id,
            'amount_tax': 0.0,
            'amount_total': -900000.0,
            'amount_paid': -900000.0,
            'amount_return': 0.0,
            'is_refund': True,
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'price_unit': 1000000.0,
                'price_subtotal': -1000000.0,
                'price_subtotal_incl': -1000000.0,
                'qty': -1,
                'tax_ids': [(5, 0, 0)],
                'refunded_orderline_id': product_line.id,
                'full_product_name': self.product.name,
            })],
            'payment_ids': [(0, 0, {
                'payment_method_id': self.bank_pm1.id,
                'amount': -900000.0,
            })],
        })
        refund.action_pos_order_paid()
        self.assertEqual(usage.state, 'cancelled')
        self.assertEqual(usage.claim_state, 'cancelled')
        self.assertEqual(quota.used_count, 1)

    def test_refund_returns_quota_when_configured(self):
        self.open_new_session()
        self.program.refund_returns_quota = True
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        quota = usage.quota_id
        order = self.make_order(self.program, 100000.0, usage_ref=str(usage.id))
        order.action_pos_order_paid()
        self.assertEqual(quota.used_count, 1)
        usage.cancel_for_refund()
        self.assertEqual(quota.used_count, 0)
        self.assertEqual(quota.used_amount, 0.0)

    def test_closing_reclassifies_only_the_bank_share(self):
        self.open_new_session()
        usage = self.reserve(self.program_half, '900350', '9876', 1000000.0)
        self.assertEqual(usage.discount_amount, 75000.0)
        self.assertEqual(usage.bank_share_amount, 37500.0)
        order = self.make_order(self.program_half, 75000.0, usage_ref=str(usage.id),
                                card_bin='900350', last4='9876')
        order.action_pos_order_paid()
        self.pos_session.post_closing_cash_details(0)
        self.pos_session.close_session_from_ui()
        move = usage.reclass_move_id
        self.assertTrue(move)
        self.assertEqual(move.state, 'posted')
        debit = move.line_ids.filtered(lambda line: line.account_id == self.claim_account)
        credit = move.line_ids.filtered(lambda line: line.account_id == self.discount_account)
        self.assertEqual(debit.debit, 37500.0)
        self.assertEqual(debit.partner_id, self.bank_b)
        self.assertEqual(credit.credit, 37500.0)
        discount_lines = (self.pos_session.move_id | move).line_ids.filtered(
            lambda line: line.account_id == self.discount_account)
        self.assertEqual(
            sum(discount_lines.mapped('debit')) - sum(discount_lines.mapped('credit')),
            37500.0)

    def test_refund_after_closing_reverses_the_reclassification(self):
        self.open_new_session()
        usage = self.reserve(self.program_half, '900350', '9876', 1000000.0)
        order = self.make_order(self.program_half, 75000.0, usage_ref=str(usage.id),
                                card_bin='900350', last4='9876')
        order.action_pos_order_paid()
        self.pos_session.post_closing_cash_details(0)
        self.pos_session.close_session_from_ui()
        self.assertEqual(usage.reclass_move_id.state, 'posted')
        sale_session = self.pos_session
        self.open_new_session()
        product_line = order.lines.filtered(lambda line: not line.is_bank_card_discount)
        discount_line = order.lines.filtered('is_bank_card_discount')
        refund = self.env['pos.order'].create({
            'session_id': self.pos_session.id,
            'company_id': self.company.id,
            'amount_tax': 0.0,
            'amount_total': -925000.0,
            'amount_paid': -925000.0,
            'amount_return': 0.0,
            'is_refund': True,
            'lines': [
                (0, 0, {
                    'product_id': self.product.id,
                    'price_unit': 1000000.0,
                    'price_subtotal': -1000000.0,
                    'price_subtotal_incl': -1000000.0,
                    'qty': -1,
                    'tax_ids': [(5, 0, 0)],
                    'refunded_orderline_id': product_line.id,
                    'full_product_name': self.product.name,
                }),
                (0, 0, {
                    'product_id': self.discount_product.id,
                    'price_unit': -75000.0,
                    'price_subtotal': 75000.0,
                    'price_subtotal_incl': 75000.0,
                    'qty': -1,
                    'tax_ids': [(5, 0, 0)],
                    'refunded_orderline_id': discount_line.id,
                    'full_product_name': self.discount_product.name,
                }),
            ],
            'payment_ids': [(0, 0, {
                'payment_method_id': self.bank_pm1.id,
                'amount': -925000.0,
            })],
        })
        refund.action_pos_order_paid()
        self.assertEqual(usage.state, 'cancelled')
        reversal = usage.reclass_reversal_move_id
        self.assertTrue(reversal)
        self.assertEqual(reversal.state, 'posted')
        claim_lines = (usage.reclass_move_id | reversal).line_ids.filtered(
            lambda line: line.account_id == self.claim_account)
        self.assertEqual(
            sum(claim_lines.mapped('debit')) - sum(claim_lines.mapped('credit')), 0.0)
        refund_session = self.pos_session
        refund_session.post_closing_cash_details(0)
        refund_session.close_session_from_ui()
        all_moves = (
            sale_session.move_id | usage.reclass_move_id | reversal | refund_session.move_id)
        discount_lines = all_moves.line_ids.filtered(
            lambda line: line.account_id == self.discount_account)
        self.assertEqual(
            sum(discount_lines.mapped('debit')) - sum(discount_lines.mapped('credit')), 0.0)
        income_line = sale_session.move_id.line_ids.filtered(
            lambda line: line.credit == 1000000.0)
        self.assertTrue(income_line)
        income_lines = all_moves.line_ids.filtered(
            lambda line: line.account_id == income_line.account_id)
        self.assertEqual(
            sum(income_lines.mapped('credit')) - sum(income_lines.mapped('debit')), 0.0)

    def test_closing_releases_open_reservations(self):
        self.open_new_session()
        usage = self.reserve(self.program, '900150', '1234', 1000000.0)
        quota = usage.quota_id
        self.assertEqual(quota.used_count, 1)
        self.pos_session.post_closing_cash_details(0)
        self.pos_session.close_session_from_ui()
        self.assertEqual(usage.state, 'cancelled')
        self.assertEqual(quota.used_count, 0)

    def test_release_quota_from_another_outlet_is_refused(self):
        usage = self.reserve(self.program_half, '900350', '9876', 1000000.0)
        result = self.env['pos.bank.card.program'].release_quota(
            usage.id, config_id=self.other_currency_config.id)
        self.assertFalse(result['released'])
        self.assertEqual(result['reason'], 'wrong_outlet')
        result = self.env['pos.bank.card.program'].release_quota(
            usage.id, config_id=self.config.id)
        self.assertTrue(result['released'])

    def test_manager_pin_verification(self):
        manager = self.env['res.users'].create({
            'name': 'Outlet Manager 1',
            'login': 'outlet_manager_1',
            'bank_card_manager_pin': '4321',
            'group_ids': [(4, self.env.ref('pos_bank_card_program.group_bank_card_manager').id)],
        })
        self.assertTrue(manager)
        self.assertTrue(manager.bank_card_manager_pin.startswith('pbkdf2$'))
        self.assertNotIn('4321', manager.bank_card_manager_pin)
        Program = self.env['pos.bank.card.program']
        self.assertTrue(Program.verify_manager_pin('4321')['verified'])
        self.assertEqual(Program.verify_manager_pin('0000')['reason'], 'wrong')
        self.assertFalse(Program.verify_manager_pin('')['verified'])
        for _index in range(4):
            Program.verify_manager_pin('0000')
        self.assertEqual(Program.verify_manager_pin('4321')['reason'], 'locked')

    def test_card_context_is_stored_on_order_fields(self):
        fields = self.env['pos.order']._load_pos_data_fields(self.config)
        for name in ('bank_card_program_id', 'bank_card_masked', 'bank_card_reservation_state',
                     'bank_card_usage_ref', 'bank_card_offline'):
            self.assertTrue(not fields or name in fields)
        line_fields = self.env['pos.order.line']._load_pos_data_fields(self.config)
        self.assertIn('is_bank_card_discount', line_fields)
        self.open_new_session()
        order = self.make_order(self.program, 100000.0, usage_ref='123')
        stored = self.env['pos.order'].browse(order.id).read(
            ['bank_card_masked', 'bank_card_reservation_state', 'bank_card_usage_ref'])[0]
        self.assertEqual(stored['bank_card_masked'], '9001 50** **** 1234')
        self.assertEqual(stored['bank_card_reservation_state'], 'reserved')
        self.assertEqual(stored['bank_card_usage_ref'], '123')
