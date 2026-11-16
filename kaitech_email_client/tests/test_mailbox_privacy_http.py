from odoo.tests import HttpCase, new_test_user, tagged

from .test_mailbox_privacy import fill_mailbox, make_mailbox

INBOX_URL = "/odoo/action-kaitech_email_client.action_umc_inbox"


@tagged("post_install", "-at_install")
class TestMailboxPrivacyHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, "umc_owner_web", groups="base.group_user", email="owner@lab0.test")
        cls.colleague = new_test_user(cls.env, "umc_colleague_web", groups="base.group_user")
        cls.admin = new_test_user(cls.env, "umc_admin_web", groups="base.group_user,base.group_system")
        cls.box = make_mailbox(cls.env, cls.owner, "Owner Web", "owner@lab0.test")
        cls.message = fill_mailbox(cls.env, cls.box, "Lab result", "<w1@client.test>")
        cls.attachment = cls.message.attachment_ids.filtered(lambda a: a.name == "result.pdf")

    def _signed_in_as(self, login):
        self.authenticate(login, login)
        overview = self.make_jsonrpc_request("/web/dataset/call_kw/umc.mailbox/get_inbox_overview", {
            "model": "umc.mailbox", "method": "get_inbox_overview", "args": [], "kwargs": {}})
        threads = self.make_jsonrpc_request("/web/dataset/call_kw/umc.thread/umc_list", {
            "model": "umc.thread", "method": "umc_list", "args": [], "kwargs": {}})
        download = self.url_open("/web/content/%s?download=true" % self.attachment.id)
        return ({b["id"] for b in overview["mailboxes"]}, [t["subject"] for t in threads["threads"]],
                download.status_code)

    def test_switching_login_shows_only_your_own_mailbox(self):
        self.assertEqual(self._signed_in_as("umc_owner_web"), ({self.box.id}, ["Lab result"], 200))
        for login in ("umc_colleague_web", "umc_admin_web"):
            with self.subTest(login=login):
                boxes, subjects, status = self._signed_in_as(login)
                self.assertEqual((boxes, subjects), (set(), []))
                self.assertIn(status, (403, 404))

    def test_inbox_screen_per_signed_in_user(self):
        self.start_tour(INBOX_URL, "kaitech_email_client_own_mailbox", login="umc_owner_web")
        self.start_tour(INBOX_URL, "kaitech_email_client_no_foreign_mailbox", login="umc_colleague_web")
        self.start_tour(INBOX_URL, "kaitech_email_client_no_foreign_mailbox", login="umc_admin_web")
