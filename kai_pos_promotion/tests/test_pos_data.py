from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import tagged

from .common import activate, tier_promo


@tagged('post_install', '-at_install')
class TestPromoPosData(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.other_config = cls.env['pos.config'].create({'name': 'Other Outlet'})
        cls.everywhere = activate(tier_promo(cls.env, 'Everywhere', [(1000, 'percent', 5)]))
        cls.outlet_only = activate(tier_promo(cls.env, 'Outlet Only', [(1000, 'percent', 6)],
                                              config_ids=[(6, 0, cls.other_config.ids)], exclusive_group='SOLO'))
        cls.draft = tier_promo(cls.env, 'Still Draft', [(1000, 'percent', 7)], exclusive_group='DRAFT')
        cls.waiting = tier_promo(cls.env, 'Waiting', [(1000, 'percent', 8)], exclusive_group='WAIT')
        cls.waiting.action_submit()
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Native Happy Hour', 'program_type': 'promotion', 'trigger': 'auto', 'applies_on': 'current',
            'kai_happy_hour': True, 'kai_schedule_ids': [(0, 0, {'hour_from': 14, 'hour_to': 16})],
            'kai_exclusive_group': 'ORDER-TIER',
        })

    def _load(self, config):
        config.open_ui()
        session = config.current_session_id
        return session.load_data([])

    def test_session_loads_only_running_promotions_of_the_outlet(self):
        data = self._load(self.config)
        names = {promo['name'] for promo in data['kai.pos.promo']}
        self.assertEqual(names, {'Everywhere'})
        tiers = data['kai.pos.promo.tier']
        self.assertEqual({tier['promo_id'] for tier in tiers}, {self.everywhere.id})
        windows = data['kai.pos.promo.schedule']
        self.assertEqual([window['program_id'] for window in windows], [self.program.id])
        program = next(p for p in data['loyalty.program'] if p['id'] == self.program.id)
        self.assertEqual(program['kai_exclusive_group'], 'ORDER-TIER')
        self.assertTrue(program['kai_happy_hour'])
        self.assertIn('kai_timezone', data['pos.config'][0])

    def test_other_outlet_gets_its_own_promotion(self):
        data = self._load(self.other_config)
        self.assertEqual({promo['name'] for promo in data['kai.pos.promo']}, {'Everywhere', 'Outlet Only'})

    def test_discount_product_reaches_the_till(self):
        data = self._load(self.config)
        loaded = {product['id'] for product in data['product.product']}
        self.assertIn(self.everywhere.discount_product_id.id, loaded)

    def test_expired_promotion_is_not_loaded(self):
        self.everywhere.flush_recordset()
        self.env.cr.execute("UPDATE kai_pos_promo SET date_end = current_date - 1 WHERE id = %s", [self.everywhere.id])
        self.everywhere.invalidate_recordset()
        data = self._load(self.config)
        self.assertFalse(data['kai.pos.promo'])
        self.assertEqual(fields.Date.today() > self.everywhere.date_end, True)

    def test_used_draft_promotion_asks_to_archive_instead_of_delete(self):
        self.config.open_ui()
        self.env['pos.order'].create({
            'session_id': self.config.current_session_id.id,
            'amount_tax': 0, 'amount_total': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({
                'product_id': self.draft.discount_product_id.id, 'qty': 1, 'price_unit': 0,
                'price_subtotal': 0, 'price_subtotal_incl': 0, 'kai_promo_id': self.draft.id,
            })],
        })
        with self.assertRaisesRegex(UserError, 'Archive it instead'):
            self.draft.unlink()
        self.draft.action_archive()
        self.assertFalse(self.draft.active)
