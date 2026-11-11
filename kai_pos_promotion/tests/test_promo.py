from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import PromoCommon, activate, tier_promo


@tagged('post_install', '-at_install')
class TestPromoLifecycle(PromoCommon):

    def test_approval_activates_a_running_promotion(self):
        self.seminyak.action_submit()
        self.assertEqual(self.seminyak.state, 'to_approve')
        self.seminyak.action_approve()
        self.assertEqual(self.seminyak.state, 'active')

    def test_approval_waits_for_the_start_date(self):
        future = tier_promo(self.env, 'Next Month', [(100000, 'percent', 5)],
                            date_start=fields.Date.add(fields.Date.today(), days=10),
                            date_end=fields.Date.add(fields.Date.today(), days=20))
        activate(future)
        self.assertEqual(future.state, 'approved')

    def test_cron_ends_and_starts_promotions(self):
        activate(self.seminyak)
        self.seminyak.flush_recordset()
        self.env.cr.execute("UPDATE kai_pos_promo SET date_end = current_date - 1 WHERE id = %s", [self.seminyak.id])
        self.seminyak.invalidate_recordset()
        self.env['kai.pos.promo']._cron_sync_states()
        self.assertEqual(self.seminyak.state, 'done')

    def test_only_drafts_change(self):
        activate(self.seminyak)
        with self.assertRaises(UserError):
            self.seminyak.name = 'Renamed'
        with self.assertRaises(UserError):
            self.seminyak.tier_ids[0].value = 50
        with self.assertRaises(UserError):
            self.seminyak.tier_ids = [Command.create({'threshold': 2000000, 'value': 25})]
        with self.assertRaises(UserError):
            self.seminyak.unlink()
        self.seminyak.action_stop()
        self.seminyak.action_reset_draft()
        self.seminyak.name = 'Renamed'
        self.assertEqual(self.seminyak.name, 'Renamed')

    def test_dragging_the_list_reorders_only_drafts(self):
        first = tier_promo(self.env, 'First Draft', [(1000, 'percent', 5)])
        second = tier_promo(self.env, 'Second Draft', [(1000, 'percent', 6)])
        activate(self.seminyak)
        dragged = self.env['kai.pos.promo'].browse([second.id, self.seminyak.id, first.id])
        rows = dragged.web_resequence({'priority': {}}, 'priority', 1)
        self.assertEqual([row['priority'] for row in rows], [1, 10, 3])
        self.assertEqual((second.priority, self.seminyak.priority, first.priority), (1, 10, 3))

    def test_only_an_approver_approves(self):
        manager = new_test_user(self.env, 'kai_manager', groups='point_of_sale.group_pos_manager')
        approver = new_test_user(self.env, 'kai_approver', groups='kai_pos_promotion.group_kai_promo_approver')
        promo = self.seminyak.with_user(manager)
        with self.assertRaises(UserError):
            self.env['kai.pos.promo'].with_user(manager).create({
                'name': 'Born Active', 'state': 'active', 'date_end': fields.Date.today()})
        with self.assertRaises(UserError):
            promo.write({'state': 'active'})
        promo.action_submit()
        with self.assertRaises(AccessError):
            promo.action_approve()
        with self.assertRaises(UserError):
            promo.write({'state': 'active'})
        with self.assertRaises(AccessError):
            promo.write({'state': 'approved'})
        self.assertEqual(self.seminyak.state, 'to_approve')
        self.seminyak.with_user(approver).action_approve()
        self.assertEqual(self.seminyak.state, 'active')
        with self.assertRaises(UserError):
            promo.write({'state': 'draft'})
        promo.action_stop()
        promo.action_reset_draft()
        self.assertEqual(self.seminyak.state, 'draft')

    def test_submit_needs_tiers_and_windows(self):
        empty = self.env['kai.pos.promo'].create({'name': 'Empty', 'date_end': fields.Date.today()})
        with self.assertRaises(UserError):
            empty.action_submit()
        empty.write({'tier_ids': [Command.create({'threshold': 1000, 'value': 5})], 'happy_hour': True})
        with self.assertRaises(UserError):
            empty.action_submit()


@tagged('post_install', '-at_install')
class TestPromoRules(PromoCommon):

    def test_tier_values(self):
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Zero', [(0, 'percent', 5)])
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Over', [(1000, 'percent', 101)])
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Too much', [(1000, 'amount', 1000)])
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Twice', [(1000, 'percent', 5), (1000, 'percent', 6)])
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Eleven', [(1000 * (i + 1), 'percent', 1) for i in range(11)])

    def test_name_unique_while_periods_overlap(self):
        with self.assertRaises(ValidationError):
            tier_promo(self.env, ' seminyak tier ', [(1000, 'percent', 5)])
        later = fields.Date.add(fields.Date.today(), days=60)
        tier_promo(self.env, 'Seminyak Tier', [(1000, 'percent', 5)], date_start=later, date_end=later)

    def test_dates_in_order(self):
        with self.assertRaises(ValidationError):
            tier_promo(self.env, 'Backwards', [(1000, 'percent', 5)],
                       date_end=fields.Date.add(fields.Date.today(), days=-1))

    def test_happy_hour_windows(self):
        Window = self.env['kai.pos.promo.schedule']
        with self.assertRaises(ValidationError):
            Window.create({'promo_id': self.seminyak.id, 'hour_from': 22, 'hour_to': 2})
        with self.assertRaises(ValidationError):
            Window.create({'promo_id': self.seminyak.id, 'hour_from': 10, 'hour_to': 12, 'day_mon': False, 'day_tue': False,
                           'day_wed': False, 'day_thu': False, 'day_fri': False, 'day_sat': False, 'day_sun': False})
        Window.create({'promo_id': self.seminyak.id, 'hour_from': 10, 'hour_to': 12})
        with self.assertRaises(ValidationError):
            Window.create({'promo_id': self.seminyak.id, 'hour_from': 11, 'hour_to': 13})
        Window.create({'promo_id': self.seminyak.id, 'hour_from': 12, 'hour_to': 13})
        with self.assertRaises(ValidationError):
            Window.create({'hour_from': 10, 'hour_to': 12})

    def test_other_company_sees_no_tier_or_window(self):
        Window = self.env['kai.pos.promo.schedule']
        Window.create({'promo_id': self.seminyak.id, 'hour_from': 10, 'hour_to': 12})
        other = self.env['res.company'].create({'name': 'Other Promo Company'})
        user = new_test_user(self.env, 'kai_other_cashier', groups='point_of_sale.group_pos_user',
                             company_id=other.id, company_ids=[(6, 0, other.ids)])
        self.assertFalse(self.env['kai.pos.promo'].with_user(user).search([('id', '=', self.seminyak.id)]))
        self.assertFalse(self.env['kai.pos.promo.tier'].with_user(user).search([('promo_id', '=', self.seminyak.id)]))
        self.assertFalse(Window.with_user(user).search([('promo_id', '=', self.seminyak.id)]))
        own = self.env['kai.pos.promo.tier'].with_user(user).search([])
        self.assertFalse(own & self.seminyak.tier_ids)

    def test_conflict_warning_lists_the_group(self):
        national = tier_promo(self.env, 'National Tier', [(400000, 'percent', 12)])
        activate(national)
        self.assertIn('National Tier', self.seminyak.conflict_html)
        program = self.env['loyalty.program'].create({
            'name': 'Native Grouped', 'program_type': 'promotion', 'kai_exclusive_group': 'ORDER-TIER'})
        self.seminyak.invalidate_recordset(['conflict_html'])
        self.assertIn(program.name, self.seminyak.conflict_html)
        alone = tier_promo(self.env, 'Alone', [(1000, 'percent', 5)], exclusive_group='SOLO')
        self.assertFalse(alone.conflict_html)

