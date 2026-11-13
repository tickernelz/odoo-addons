from odoo import Command, models
from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import load_script
from odoo.tests import Form, tagged
from odoo.tools.misc import file_path

from .common import CheckoutExtrasCommon


@tagged('post_install', '-at_install')
class TestAnalyticAccounts(CheckoutExtrasCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kuta = cls.env['account.analytic.account'].create({'name': 'Kuta', 'plan_id': cls.branch_plan.id})
        cls.env.user.group_ids |= cls.env.ref('analytic.group_analytic_accounting')
        cls.other_company = cls.setup_other_company()['company']

    def branch_shares(self):
        branch = (self.ubud | self.seminyak | self.kuta).ids
        return sorted(percent for key, percent in self.config.analytic_distribution.items() if int(key) in branch)

    def test_accounts_in_different_plans_each_take_the_whole_amount(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.assertEqual(self.config.analytic_distribution, self.ubud_retail)
        self.assertEqual(self.ubud.pos_config_ids, self.config)
        self.assertEqual(self.retail.pos_config_ids, self.config)

    def test_one_account_serves_many_counters(self):
        other = self.config.copy({'name': 'Second counter'})
        (self.config | other).write({'analytic_account_ids': [Command.set(self.seminyak.ids)]})
        self.assertEqual(self.seminyak.pos_config_ids, self.config | other)
        self.assertEqual(other.analytic_distribution, self.seminyak_only)

    def test_accounts_in_one_plan_share_it_evenly(self):
        self.config.analytic_account_ids = self.ubud | self.seminyak | self.kuta | self.retail
        self.assertEqual(self.branch_shares(), [33.33, 33.33, 33.34])
        self.assertEqual(self.config.analytic_distribution[str(self.retail.id)], 100.0)

    def test_an_adjusted_split_survives_a_change_in_another_plan(self):
        split = {str(self.ubud.id): 60.0, str(self.seminyak.id): 40.0}
        self.config.analytic_distribution = split
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.seminyak)
        self.config.write({'analytic_account_ids': [Command.link(self.retail.id)]})
        self.assertEqual(self.config.analytic_distribution, dict(split, **{str(self.retail.id): 100.0}))
        self.config.write({'analytic_account_ids': [Command.link(self.kuta.id)]})
        self.assertEqual(self.branch_shares(), [33.33, 33.33, 33.34])

    def test_a_distribution_is_kept_as_is_while_its_accounts_stay(self):
        combined = {'%s,%s' % (self.ubud.id, self.retail.id): 60.0, '%s,%s' % (self.seminyak.id, self.retail.id): 40.0}
        self.config.analytic_distribution = combined
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.seminyak | self.retail)
        self.config.write({'analytic_account_ids': [Command.set((self.ubud | self.seminyak | self.retail).ids)]})
        self.assertEqual(self.config.analytic_distribution, combined)

    def test_an_archived_account_stays_picked(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.ubud.action_archive()
        self.config.invalidate_recordset()
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.retail)
        self.config.analytic_scope = 'all'
        self.config.write({'analytic_account_ids': [Command.link(self.seminyak.id)]})
        self.assertEqual(self.branch_shares(), [50.0, 50.0])

    def test_duplicating_an_account_leaves_its_counters_alone(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        copy = self.ubud.copy()
        self.assertFalse(copy.pos_config_ids)
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.retail)
        self.assertEqual(self.config.analytic_distribution, self.ubud_retail)

    def test_linking_from_the_account_splits_and_logs(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.seminyak.pos_config_ids = self.config
        self.assertEqual(self.config.analytic_distribution, dict(self.ubud_retail, **{
            str(self.ubud.id): 50.0, str(self.seminyak.id): 50.0}))
        log = self.env['pos.analytic.sync.log'].search(
            [('config_id', '=', self.config.id), ('kind', '=', 'distribution')], limit=1)
        self.assertEqual(log.old_distribution, self.ubud_retail)
        self.assertEqual(log.new_distribution, self.config.analytic_distribution)

    def test_every_way_of_naming_counters_from_the_account_is_logged(self):
        logs = self.env['pos.analytic.sync.log']
        other = self.config.copy({'name': 'Second counter'})
        for value in ([self.config.id], (other.id,), False, self.config | other, None):
            before = logs.search_count([('kind', '=', 'distribution')])
            self.ubud.write({'pos_config_ids': value})
            expected = self.env['pos.config'].browse(
                value.ids if isinstance(value, models.BaseModel) else list(value or ()))
            self.assertEqual(self.ubud.pos_config_ids, expected)
            for config in self.config | other:
                linked = config in expected
                self.assertEqual(config.analytic_distribution, {str(self.ubud.id): 100.0} if linked else False)
            self.assertGreater(logs.search_count([('kind', '=', 'distribution')]), before)
        created = self.env['account.analytic.account'].create({
            'name': 'Created linked', 'plan_id': self.channel_plan.id, 'pos_config_ids': self.config | other})
        self.assertEqual(other.analytic_distribution, {str(created.id): 100.0})

    def test_an_account_of_another_company_cannot_link_a_counter(self):
        foreign = self.env['account.analytic.account'].create({
            'name': 'Foreign', 'plan_id': self.branch_plan.id, 'company_id': self.other_company.id})
        with self.assertRaises(UserError):
            foreign.pos_config_ids = self.config
        with self.assertRaises(UserError):
            self.env['account.analytic.account'].create({
                'name': 'Foreign too', 'plan_id': self.branch_plan.id, 'company_id': self.other_company.id,
                'pos_config_ids': [Command.link(self.config.id)]})
        self.assertFalse(self.config.analytic_account_ids)

    def test_linking_from_the_account_respects_a_mandatory_plan(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.branch_plan.write({'applicability_ids': [(0, 0, {
            'business_domain': 'general',
            'applicability': 'mandatory',
        })]})
        with self.assertRaisesRegex(ValidationError, '100%'):
            self.ubud.pos_config_ids = False

    def test_deleting_an_account_splits_its_plan_again(self):
        self.config.analytic_distribution = {str(self.ubud.id): 60.0, str(self.kuta.id): 40.0}
        self.kuta.unlink()
        self.assertEqual(self.config.analytic_account_ids, self.ubud)
        self.assertEqual(self.config.analytic_distribution, {str(self.ubud.id): 100.0})

    def test_a_split_naming_a_deleted_account_is_redone_on_change(self):
        gone = self.env['account.analytic.account'].create({'name': 'Gone', 'plan_id': self.branch_plan.id})
        gone_id = gone.id
        self.config.analytic_distribution = {str(self.ubud.id): 60.0, str(gone_id): 40.0}
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM pos_config_analytic_account_rel WHERE account_id = %s", [gone_id])
        self.env.cr.execute("DELETE FROM account_analytic_account WHERE id = %s", [gone_id])
        self.env.invalidate_all()
        self.config.write({'analytic_account_ids': [Command.link(self.retail.id)]})
        self.assertEqual(self.config.analytic_distribution, self.ubud_retail)

    def test_writing_a_split_leaves_the_callers_values_alone(self):
        vals = {'analytic_distribution': self.seminyak_only}
        self.config.write(vals)
        self.assertEqual(vals, {'analytic_distribution': self.seminyak_only})
        self.assertEqual(self.config.analytic_account_ids, self.seminyak)

    def test_removing_every_account_clears_the_distribution(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.config.analytic_account_ids = False
        self.assertFalse(self.config.analytic_distribution)

    def test_changing_the_accounts_is_logged(self):
        self.config.analytic_account_ids = self.ubud | self.retail
        self.config.analytic_account_ids = self.seminyak
        log = self.env['pos.analytic.sync.log'].search(
            [('config_id', '=', self.config.id), ('kind', '=', 'distribution')], limit=1)
        self.assertEqual(log.old_distribution, self.ubud_retail)
        self.assertEqual(log.new_distribution, self.seminyak_only)

    def test_the_split_must_use_exactly_the_accounts(self):
        with self.assertRaisesRegex(ValidationError, 'exactly its analytic accounts'):
            self.config.write({
                'analytic_account_ids': [Command.set(self.ubud.ids)],
                'analytic_distribution': self.seminyak_only,
            })

    def test_an_account_of_another_company_is_refused(self):
        foreign = self.env['account.analytic.account'].create({
            'name': 'Foreign', 'plan_id': self.branch_plan.id, 'company_id': self.other_company.id})
        with self.assertRaises(UserError):
            self.config.analytic_account_ids = foreign

    def test_point_of_sale_form_keeps_accounts_and_split_together(self):
        with Form(self.config) as form:
            form.analytic_account_ids.add(self.ubud)
            form.analytic_account_ids.add(self.seminyak)
            self.assertEqual(form.analytic_distribution, {str(self.ubud.id): 50.0, str(self.seminyak.id): 50.0})
            form.analytic_distribution = {str(self.ubud.id): 70.0, str(self.seminyak.id): 30.0, str(self.retail.id): 100.0}
            self.assertEqual(set(form.analytic_account_ids.ids), set((self.ubud | self.seminyak | self.retail).ids))
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.seminyak | self.retail)
        self.assertEqual(self.config.analytic_distribution[str(self.ubud.id)], 70.0)

    def test_settings_screen_saves_the_chosen_accounts(self):
        self.config.analytic_distribution = {str(self.ubud.id): 60.0, str(self.seminyak.id): 40.0}
        form = Form(self.env['res.config.settings'])
        form.pos_config_id = self.config
        self.assertEqual(set(form.pos_analytic_account_ids.ids), set((self.ubud | self.seminyak).ids))
        form.pos_analytic_account_ids.add(self.retail)
        self.assertEqual(form.pos_analytic_distribution[str(self.ubud.id)], 60.0)
        self.assertEqual(form.pos_analytic_distribution[str(self.retail.id)], 100.0)
        form.pos_analytic_account_ids.remove(id=self.seminyak.id)
        self.assertEqual(form.pos_analytic_distribution, self.ubud_retail)
        form.save()
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.retail)
        self.assertEqual(self.config.analytic_distribution, self.ubud_retail)

    def test_upgrade_picks_the_accounts_of_each_distribution(self):
        combined = {'%s,%s' % (self.ubud.id, self.retail.id): 60.0, '%s,%s' % (self.seminyak.id, self.retail.id): 40.0}
        gone = self.env['account.analytic.account'].create({'name': 'Gone', 'plan_id': self.channel_plan.id})
        gone_id = gone.id
        gone.unlink()
        bare = self.config.copy({'name': 'Bare counter'})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pos_config SET analytic_distribution = %s::jsonb WHERE id = %s",
            ['{"%s,%s": 60.0, "%s,%s": 40.0, "%s": 100.0}' % (
                self.ubud.id, self.retail.id, self.seminyak.id, self.retail.id, gone_id), self.config.id])
        self.env.cr.execute("DELETE FROM pos_config_analytic_account_rel")
        self.env.invalidate_all()
        script = load_script(file_path('pos_checkout_extras/migrations/19.0.1.1.0/post-migrate.py'), 'pos_checkout_extras')
        script.migrate(self.env.cr, '19.0.1.0.0')
        self.env.invalidate_all()
        self.assertEqual(self.config.analytic_account_ids, self.ubud | self.seminyak | self.retail)
        self.assertEqual(self.config.analytic_distribution, dict(combined, **{str(gone_id): 100.0}))
        self.assertFalse(bare.analytic_account_ids)
