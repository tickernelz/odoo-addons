from odoo.tests import HttpCase, new_test_user, tagged

from .test_email_client import raw_mail


@tagged("post_install", "-at_install")
class TestInboxTour(HttpCase):

    def test_inbox_tags_links_and_reply(self):
        user = new_test_user(self.env, "umc_tour", groups="base.group_user,base.group_partner_manager",
                             name="Tour User")
        box = self.env["umc.mailbox"].create({
            "name": "Sales", "email": "sales@lab0.test", "mailbox_type": "team", "account_type": "imap",
            "imap_host": "imap.invalid", "smtp_host": "smtp.invalid", "password": "x"})
        self.env["umc.mailbox.member"].create({"mailbox_id": box.id, "user_id": user.id, "role": "reply"})
        inbox = self.env["umc.folder"].create({"mailbox_id": box.id, "name": "INBOX", "path": "INBOX",
                                               "role": "inbox", "sync": True})
        message = box._import_message(inbox, 1, b"", raw_mail("Quotation 50 units"))
        message.thread_id._refresh_stats()
        inbox._refresh_counters()
        self.env["res.partner"].create({"name": "Acme Trading"})
        self.env["umc.signature"].with_user(user).create({"name": "Me", "body": "<p>Reply Person</p>",
                                                          "is_default": True})
        self.start_tour("/odoo/action-kaitech_email_client.action_umc_inbox", "kaitech_email_client_inbox",
                        login="umc_tour")
