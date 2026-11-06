# -*- coding: utf-8 -*-
from odoo.tests import common

from .common import new_user


@common.at_install(False)
@common.post_install(True)
class TestMenus(common.TransactionCase):

    def top_menus(self, user):
        return {menu['id']: menu for menu in self.env['ir.ui.menu'].sudo(user).load_menus(False)['children']}

    def test_every_mcp_screen_lives_under_its_own_app(self):
        root = self.env.ref('kaitech_mcp_server.menu_mcp_root')
        self.assertFalse(root.parent_id)
        names = self.env['ir.model.data'].search([('module', '=', 'kaitech_mcp_server'), ('model', '=', 'ir.ui.menu')])
        menus = self.env['ir.ui.menu'].browse(names.mapped('res_id'))
        self.assertGreater(len(menus), 1)
        for menu in menus:
            top = menu
            while top.parent_id:
                top = top.parent_id
            self.assertEqual(top, root, menu.complete_name)

    def test_the_app_shows_with_its_icon_only_to_mcp_groups(self):
        root = self.env.ref('kaitech_mcp_server.menu_mcp_root')
        auditor = new_user(self.env, 'mcp_menu_auditor', groups=('kaitech_mcp_server.group_mcp_auditor',))
        employee = new_user(self.env, 'mcp_menu_employee', groups=('base.group_user',))
        app = self.top_menus(auditor).get(root.id)
        self.assertTrue(app)
        self.assertTrue(app['web_icon_data'])
        self.assertNotIn(root.id, self.top_menus(employee))
