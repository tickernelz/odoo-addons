from odoo.tests import HttpCase, tagged

from .common import new_user


@tagged('post_install', '-at_install')
class TestPortalAccess(HttpCase):

    def setUp(self):
        super().setUp()
        self.owner = new_user(self.env, 'mcp_portal_owner')
        self.other = new_user(self.env, 'mcp_portal_other')
        self.page = self.env['mcp.portal.page'].with_user(self.owner).create({'name': 'Owner KPIs', 'spec': '{}'})
        self.artifact = self.env['mcp.html.artifact'].with_user(self.owner).create(
            {'name': 'Owner chart', 'html': '<p>owner only</p>'})

    def status(self, url):
        return self.url_open(url, allow_redirects=False).status_code

    def test_a_private_page_shows_to_its_owner_and_token_holders_only(self):
        for kind, record in (('mcp-page', self.page), ('mcp-artifact', self.artifact)):
            url = '/%s/%s' % (kind, record.slug)
            self.authenticate(None, None)
            self.assertEqual(self.status(url), 302, url)
            self.assertEqual(self.status('%s?token=wrong' % url), 302, url)
            self.assertEqual(self.status('%s?token=%s' % (url, record.access_token)), 200, url)
            self.authenticate('mcp_portal_other', 'mcp_portal_other' * 2)
            self.assertEqual(self.status(url), 404, url)
            self.authenticate('mcp_portal_owner', 'mcp_portal_owner' * 2)
            self.assertEqual(self.status(url), 200, url)
