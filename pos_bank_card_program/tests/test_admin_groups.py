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

    def test_admin_gets_module_groups_on_install(self):
        self.assertIn(self.manager_group, self.admin.group_ids)
        self.assertIn(self.finance_group, self.admin.group_ids)

    def test_plain_user_gets_no_module_groups(self):
        user = self.env['res.users'].create({'name': 'Plain User', 'login': 'plain_user'})
        self.assertNotIn(self.manager_group, user.group_ids)
        self.assertNotIn(self.finance_group, user.group_ids)

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
