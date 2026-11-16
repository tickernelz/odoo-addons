from email.message import EmailMessage
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

from ..models.umc_signature import replace_signature, signature_block


class FakeSmtp:
    sent = []

    def send_message(self, msg, from_addr=None, to_addrs=None):
        FakeSmtp.sent.append((msg, from_addr, to_addrs))

    def quit(self):
        pass


class FakeImap:
    appended = []

    def append(self, mailbox, flags, date_time, message):
        FakeImap.appended.append(mailbox)

    def logout(self):
        pass


def raw_mail(subject, sender="Client One <client@client.test>", to="sales@lab0.test", body="Hello team",
             message_id="<m1@client.test>", in_reply_to=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    msg.add_alternative("<p>%s</p>" % body, subtype="html")
    return msg.as_bytes()


@tagged("post_install", "-at_install")
class TestEmailClient(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.reply_user = new_test_user(cls.env, "umc_reply", groups="base.group_user,base.group_partner_manager",
                                       email="reply@lab0.test")
        cls.read_user = new_test_user(cls.env, "umc_read", groups="base.group_user")
        cls.outsider = new_test_user(cls.env, "umc_out", groups="base.group_user")
        cls.box = cls.env["umc.mailbox"].create({
            "name": "Sales", "email": "Sales@Lab0.test", "mailbox_type": "team", "account_type": "imap",
            "imap_host": "imap.invalid", "smtp_host": "smtp.invalid", "password": "x",
        })
        cls.env["umc.mailbox.member"].create([
            {"mailbox_id": cls.box.id, "user_id": cls.reply_user.id, "role": "reply"},
            {"mailbox_id": cls.box.id, "user_id": cls.read_user.id, "role": "read"},
        ])
        cls.inbox = cls.env["umc.folder"].create({"mailbox_id": cls.box.id, "name": "INBOX", "path": "INBOX",
                                                  "role": "inbox", "sync": True})
        cls.sent_folder = cls.env["umc.folder"].create({"mailbox_id": cls.box.id, "name": "Sent", "path": "Sent",
                                                        "role": "sent", "sync": True})
        cls.message = cls.box._import_message(cls.inbox, 1, b"", raw_mail("Quotation 50 units",
                                                                           body="Please quote 50 units of X-200"))
        cls.thread = cls.message.thread_id
        cls.box.folder_ids._refresh_counters()
        cls.thread._refresh_stats()

    def _send_reply(self, user, **values):
        composer = self.env["umc.composer"].with_user(user).with_context(
            umc_message_id=self.message.id, umc_mode="reply").create(values)
        FakeSmtp.sent = []
        with patch.object(type(self.env["umc.mailbox"]), "_smtp_connect", lambda box: FakeSmtp()), \
                patch.object(type(self.env["umc.mailbox"]), "_imap_connect", lambda box: FakeImap()):
            composer.action_send()
        return composer

    def test_works_without_any_plan(self):
        self.assertNotIn("umc.license", self.env)
        self.assertEqual(self.box.email, "sales@lab0.test")
        self.assertEqual(self.env["ir.cron"].search_count([("name", "ilike", "UNIR2")]), 0)

    def test_imported_mail_reaches_the_team_only(self):
        self.assertEqual(self.thread.partner_id.email, "client@client.test")
        listed = self.env["umc.thread"].with_user(self.read_user).umc_list(mailbox_id=self.box.id)
        self.assertEqual([t["subject"] for t in listed["threads"]], ["Quotation 50 units"])
        self.assertEqual(self.env["umc.thread"].with_user(self.outsider).search_count([]), 0)
        with self.assertRaises(AccessError):
            self.env["umc.thread"].with_user(self.outsider).umc_open(self.thread.id)

    def test_reply_goes_out_with_threading_headers(self):
        self._send_reply(self.reply_user)
        msg, sender, rcpt = FakeSmtp.sent[0]
        self.assertEqual(msg["Subject"], "Re: Quotation 50 units")
        self.assertEqual(msg["In-Reply-To"], "<m1@client.test>")
        self.assertEqual(rcpt, ["client@client.test"])
        self.assertEqual(self.thread.message_count, 2)

    def test_read_only_member_cannot_send(self):
        with self.assertRaises(AccessError):
            self._send_reply(self.read_user, mailbox_id=self.box.id)
        self.assertEqual(self.thread.message_count, 1)

    def test_signature_default_and_swap(self):
        Signature = self.env["umc.signature"].with_user(self.reply_user)
        shared = self.env["umc.signature"].create({"name": "Team", "body": "<p>Sales team</p>", "owner_id": False,
                                                   "mailbox_id": self.box.id, "is_default": True})
        self.assertEqual(Signature._default_for(self.box), shared)
        mine = Signature.create({"name": "Me", "body": "<p>Reply Person</p>", "is_default": True})
        self.assertEqual(Signature._default_for(self.box), mine)
        form = Form(self.env["umc.composer"].with_user(self.reply_user).with_context(
            umc_message_id=self.message.id, umc_mode="reply"))
        self.assertIn("Reply Person", str(form.body))
        form.signature_id = shared
        self.assertIn("Sales team", str(form.body))
        self.assertNotIn("Reply Person", str(form.body))
        self.assertIn("X-200", str(form.body))

    def test_replace_signature_inserts_before_quote(self):
        body = '<p>Hi</p><div class="umc-quote"><p>old</p></div>'
        out = str(replace_signature(body, signature_block("<p>Sig</p>")))
        self.assertLess(out.index("Sig"), out.index("old"))
        self.assertGreater(out.index("Sig"), out.index("Hi"))

    def test_signature_of_another_user_is_hidden(self):
        mine = self.env["umc.signature"].with_user(self.reply_user).create({"name": "Me", "body": "<p>x</p>"})
        self.assertFalse(self.env["umc.signature"].with_user(self.read_user).search([("id", "=", mine.id)]))

    def test_partner_template_fills_the_contact(self):
        template = self.env["mail.template"].create({
            "name": "Quote follow-up", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "subject": "Offer for {{ object.name }}", "body_html": "<p>Dear <t t-out='object.name'/>,</p>"})
        form = Form(self.env["umc.composer"].with_context(umc_message_id=self.message.id, umc_mode="reply"))
        form.template_id = template
        self.assertEqual(form.subject, "Offer for Client One")
        self.assertIn("Dear Client One", str(form.body))
        self.assertIn("X-200", str(form.body))

    def test_link_logs_the_email_on_the_record_chatter(self):
        partner = self.env["res.partner"].create({"name": "Acme"})
        threads = self.env["umc.thread"].with_user(self.reply_user)
        links = threads.umc_link(self.thread.id, "res.partner", partner.id)
        self.assertEqual([(lk["name"], lk["primary"]) for lk in links], [("Acme", True)])
        note = partner.message_ids.filtered(lambda m: "X-200" in str(m.body))
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(threads.umc_unlink(links[0]["id"]), [])
        self.assertTrue(partner.exists())

    def test_read_only_member_cannot_link(self):
        partner = self.env["res.partner"].create({"name": "Acme"})
        with self.assertRaises(AccessError):
            self.env["umc.thread"].with_user(self.read_user).umc_link(self.thread.id, "res.partner", partner.id)

    def test_link_refuses_unlisted_model(self):
        with self.assertRaises(UserError):
            self.env["umc.thread"].umc_link(self.thread.id, "res.users", self.env.uid)

    def test_reply_is_logged_on_the_primary_record(self):
        partner = self.env["res.partner"].create({"name": "Acme"})
        self.env["umc.thread"].umc_link(self.thread.id, "res.partner", partner.id)
        composer = self._send_reply(self.reply_user)
        self.assertEqual(composer.link_ref, partner)
        self.assertTrue(partner.message_ids.filtered(lambda m: "Re: Quotation" in str(m.body)))

    def test_create_task_from_email(self):
        if "project.task" not in self.env:
            self.skipTest("project is not installed")
        project = self.env["project.project"].create({"name": "Support"})
        res = self.env["umc.thread"].umc_create_record(self.thread.id, "project.task", project.id)
        task = self.env["project.task"].browse(res["record"]["res_id"])
        self.assertEqual((task.name, task.project_id, task.partner_id), ("Quotation 50 units", project,
                                                                          self.thread.partner_id))
        self.assertIn("X-200", str(task.description))
        self.assertTrue(res["links"][0]["primary"])

    def test_create_lead_from_email(self):
        if "crm.lead" not in self.env:
            self.skipTest("crm is not installed")
        res = self.env["umc.thread"].umc_create_record(self.thread.id, "crm.lead")
        lead = self.env["crm.lead"].browse(res["record"]["res_id"])
        self.assertEqual((lead.name, lead.email_from), ("Quotation 50 units", "client@client.test"))

    def test_filters_tags_and_body_search(self):
        out = self.box._import_message(self.sent_folder, 7, b"\\Seen \\Flagged", raw_mail(
            "Invoice INV/9", sender="sales@lab0.test", to="other@client.test", body="Payment terms",
            message_id="<m2@lab0.test>"))
        out.thread_id._refresh_stats()
        Thread = self.env["umc.thread"].with_user(self.reply_user)

        def subjects(**kw):
            return sorted(t["subject"] for t in Thread.umc_list(mailbox_id=self.box.id, folder_id=False,
                                                                 role=kw.pop("role", "inbox"), **kw)["threads"])
        self.assertEqual(subjects(role="sent", list_filter="out"), ["Invoice INV/9"])
        self.assertEqual(subjects(role="sent", list_filter="in"), [])
        self.assertEqual(subjects(role="sent", list_filter="starred"), ["Invoice INV/9"])
        self.assertEqual(subjects(search="X-200"), ["Quotation 50 units"])
        tag = self.env["umc.tag"].with_user(self.reply_user).create({"name": "Waiting"})
        Thread.umc_set_tags([self.thread.id], [tag.id])
        self.assertEqual(subjects(tag_id=tag.id), ["Quotation 50 units"])
        with self.assertRaises(AccessError):
            self.env["umc.thread"].with_user(self.read_user).umc_set_tags([self.thread.id], [])
