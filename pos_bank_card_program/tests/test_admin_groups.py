from odoo import Command
from odoo.tests import TransactionCase, tagged
from odoo.tools.convert import convert_file


@tagged('post_install', '-at_install')
class TestAdminGroups(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.manager_group = cls.env.ref('pos_bank_card_program.group_bank_card_manager')
        cls.finance_group = cls.env.ref('pos_bank_card_program.group_bank_card_finance')

    def module_group_ids(self):
        return {self.manager_group.id, self.finance_group.id}

    def user_form_selection(self, held_group_ids):
        privileges = self.env['res.groups']._get_view_group_hierarchy()['privileges']
        selection = set()
        for privilege in privileges.values():
            chosen = next(
                (gid for gid in reversed(privilege['group_ids']) if gid in held_group_ids), None)
            if chosen:
                selection.add(chosen)
        return selection

    def test_admin_gets_module_groups_on_install(self):
        self.assertIn(self.manager_group, self.admin.group_ids)
        self.assertIn(self.finance_group, self.admin.group_ids)

    def test_plain_user_gets_no_module_groups(self):
        user = self.env['res.users'].create({'name': 'Plain User', 'login': 'plain_user'})
        self.assertNotIn(self.manager_group, user.group_ids)
        self.assertNotIn(self.finance_group, user.group_ids)

    def test_admin_module_groups_are_shown_separately(self):
        module_groups = self.module_group_ids()
        shown = self.user_form_selection(set(self.admin.group_ids.ids)) & module_groups
        self.assertEqual(shown, module_groups)

    def test_admin_module_groups_survive_a_user_form_save(self):
        held = set(self.admin.group_ids.ids)
        shown = self.user_form_selection(held)
        self.admin.write({'group_ids': [Command.set(sorted((held - self.module_group_ids()) | shown))]})
        self.assertIn(self.manager_group, self.admin.group_ids)
        self.assertIn(self.finance_group, self.admin.group_ids)

    def test_upgrade_does_not_restore_revoked_admin_groups(self):
        self.admin.group_ids = [
            Command.unlink(self.manager_group.id),
            Command.unlink(self.finance_group.id),
        ]
        convert_file(
            self.env, 'pos_bank_card_program', 'security/security.xml', {},
            mode='update', noupdate=False)
        self.admin.invalidate_recordset(['group_ids'])
        self.assertNotIn(self.manager_group, self.admin.group_ids)
        self.assertNotIn(self.finance_group, self.admin.group_ids)
