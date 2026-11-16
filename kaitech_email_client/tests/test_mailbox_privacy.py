from email.message import EmailMessage

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


def mail_with_file(subject, body="Private salary review", message_id="<p1@client.test>"):
    msg = EmailMessage()
    msg["From"] = "Doctor <doctor@clinic.test>"
    msg["To"] = "owner@lab0.test"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg.set_content(body)
    msg.add_attachment(b"%PDF-1.4 private", maintype="application", subtype="pdf", filename="result.pdf")
    return msg.as_bytes()


def make_mailbox(env, owner, name, email, mailbox_type="personal"):
    return env["umc.mailbox"].create({
        "name": name, "email": email, "mailbox_type": mailbox_type, "owner_id": owner.id,
        "account_type": "imap", "imap_host": "imap.invalid", "smtp_host": "smtp.invalid", "password": "x",
    })


def fill_mailbox(env, box, subject, message_id):
    inbox = env["umc.folder"].create({"mailbox_id": box.id, "name": "INBOX", "path": "INBOX",
                                      "role": "inbox", "sync": True})
    message = box._import_message(inbox, 1, b"", mail_with_file(subject, message_id=message_id))
    message.thread_id._refresh_stats()
    inbox._refresh_counters()
    partner = env["res.partner"].create({"name": "Clinic"})
    env["umc.record.link"].create({"thread_id": message.thread_id.id, "res_model": "res.partner",
                                   "res_id": partner.id, "is_primary": True})
    return message


@tagged("post_install", "-at_install")
class TestMailboxPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.owner = new_test_user(cls.env, "umc_owner", groups="base.group_user", email="owner@lab0.test")
        cls.colleague = new_test_user(cls.env, "umc_colleague", groups="base.group_user")
        cls.admin = new_test_user(cls.env, "umc_admin", groups="base.group_user,base.group_system")
        cls.personal = make_mailbox(cls.env, cls.owner, "Owner", "owner@lab0.test")
        cls.message = fill_mailbox(cls.env, cls.personal, "Lab result", "<p1@client.test>")
        cls.attachment = cls.message.attachment_ids.filtered(lambda a: a.name == "result.pdf")
        cls.team = make_mailbox(cls.env, cls.owner, "Sales", "sales@lab0.test", mailbox_type="team")
        cls.env["umc.mailbox.member"].create({"mailbox_id": cls.team.id, "user_id": cls.colleague.id,
                                              "role": "read"})
        cls.team_message = fill_mailbox(cls.env, cls.team, "Quotation", "<t1@client.test>")

    def _content_seen_by(self, user, box):
        domain = [("mailbox_id", "=", box.id)]
        return {model: self.env[model].with_user(user).search_count(domain)
                for model in ("umc.folder", "umc.thread", "umc.message", "umc.record.link")}

    def _inbox_of(self, user):
        return {b["id"] for b in self.env["umc.mailbox"].with_user(user).get_inbox_overview()["mailboxes"]}

    def test_owner_reads_the_personal_mailbox(self):
        self.assertEqual(self._content_seen_by(self.owner, self.personal),
                         {"umc.folder": 1, "umc.thread": 1, "umc.message": 1, "umc.record.link": 1})
        self.assertIn(self.personal.id, self._inbox_of(self.owner))
        self.assertEqual(self.attachment.with_user(self.owner).raw, b"%PDF-1.4 private")

    def test_personal_mailbox_is_closed_to_everyone_else(self):
        self.assertTrue(self.attachment)
        for user in (self.colleague, self.admin):
            with self.subTest(user=user.login):
                self.assertEqual(self._content_seen_by(user, self.personal),
                                 {"umc.folder": 0, "umc.thread": 0, "umc.message": 0, "umc.record.link": 0})
                self.assertNotIn(self.personal.id, self._inbox_of(user))
                with self.assertRaises(AccessError):
                    self.env["umc.thread"].with_user(user).umc_open(self.message.thread_id.id)
                with self.assertRaises(AccessError):
                    self.attachment.with_user(user).read(["raw"])
                self.assertFalse(self.env["ir.attachment"].with_user(user).search(
                    [("res_model", "=", "umc.message"), ("res_id", "=", self.message.id)]))
                with self.assertRaises(AccessError):
                    self.personal.with_user(user).umc_sync_now()
        self.assertFalse(self.env["umc.mailbox"].with_user(self.colleague).search([("id", "=", self.personal.id)]))
        self.assertFalse(self.personal.with_user(self.admin).my_role)
        self.assertFalse(self.personal.with_user(self.admin).can_configure)

    def test_composer_never_offers_someone_elses_mailbox(self):
        Composer = self.env["umc.composer"].with_user(self.admin)
        self.assertNotIn(self.personal, Composer.new({}).allowed_mailbox_ids)
        self.assertFalse(Composer.default_get(["mailbox_id"]).get("mailbox_id"))
        composer = Composer.create({"mailbox_id": self.personal.id, "to_emails": "doctor@clinic.test",
                                    "subject": "Hello", "body": "<p>Hi</p>"})
        with self.assertRaises(AccessError):
            composer.action_send()

    def test_admin_configures_but_cannot_take_a_personal_mailbox(self):
        box = self.personal.with_user(self.admin)
        with self.assertRaises(AccessError):
            self.env["umc.mailbox.member"].with_user(self.admin).create(
                {"mailbox_id": self.personal.id, "user_id": self.admin.id, "role": "read"})
        for vals in ({"owner_id": self.admin.id}, {"mailbox_type": "team"}, {"imap_host": "imap.elsewhere"}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                box.write(vals)
        box.write({"active": False})
        self.assertFalse(self.personal.active)

    def test_team_mailbox_opens_only_for_assigned_users(self):
        self.assertEqual(self._content_seen_by(self.colleague, self.team)["umc.message"], 1)
        self.assertIn(self.team.id, self._inbox_of(self.colleague))
        self.assertEqual(self._content_seen_by(self.admin, self.team),
                         {"umc.folder": 0, "umc.thread": 0, "umc.message": 0, "umc.record.link": 0})
        self.assertNotIn(self.team.id, self._inbox_of(self.admin))
        self.assertTrue(self.team.with_user(self.admin).can_configure)
        self.env["umc.mailbox.member"].with_user(self.admin).create(
            {"mailbox_id": self.team.id, "user_id": self.admin.id, "role": "read"})
        self.env.flush_all()
        self.assertEqual(self._content_seen_by(self.admin, self.team)["umc.message"], 1)

    def test_personal_mailbox_ignores_leftover_members(self):
        self.personal.with_user(self.owner).write({"mailbox_type": "team"})
        self.env["umc.mailbox.member"].with_user(self.owner).create(
            {"mailbox_id": self.personal.id, "user_id": self.colleague.id, "role": "read"})
        self.env.flush_all()
        self.assertEqual(self._content_seen_by(self.colleague, self.personal)["umc.message"], 1)
        self.personal.with_user(self.owner).write({"mailbox_type": "personal"})
        self.env.flush_all()
        self.assertEqual(self._content_seen_by(self.colleague, self.personal)["umc.message"], 0)
        self.assertNotIn(self.personal.id, self._inbox_of(self.colleague))

    def test_member_row_cannot_be_moved_into_another_mailbox(self):
        own = make_mailbox(self.env, self.colleague, "Own team", "own@lab0.test", mailbox_type="team")
        row = self.env["umc.mailbox.member"].with_user(self.colleague).create(
            {"mailbox_id": own.id, "user_id": self.admin.id, "role": "read"})
        with self.assertRaises(AccessError), self.cr.savepoint():
            row.write({"mailbox_id": self.team.id, "role": "manage"})
        self.env.flush_all()
        self.assertEqual(row.mailbox_id, own)
        self.assertEqual(self._content_seen_by(self.admin, self.team)["umc.message"], 0)
        self.assertFalse(self.team.with_user(self.admin).my_role)

    def test_folder_cannot_be_moved_into_another_mailbox(self):
        own = make_mailbox(self.env, self.colleague, "Own", "own@lab0.test")
        folder = self.env["umc.folder"].create({"mailbox_id": own.id, "name": "Planted", "path": "Planted"})
        with self.assertRaises(AccessError), self.cr.savepoint():
            folder.with_user(self.colleague).write({"mailbox_id": self.personal.id})
        self.assertEqual(folder.mailbox_id, own)

    def test_admin_cannot_move_content_into_another_mailbox(self):
        own = make_mailbox(self.env, self.admin, "Admin", "admin@lab0.test")
        mine = fill_mailbox(self.env, own, "Planted", "<a1@client.test>")
        mine.uid = "planted"
        thread, link = mine.thread_id.with_user(self.admin), mine.thread_id.link_ids.with_user(self.admin)
        message = mine.with_user(self.admin)
        foreign_thread, foreign_folder = self.message.thread_id, self.message.folder_id
        attempts = [(thread, {"mailbox_id": self.personal.id}), (message, {"thread_id": foreign_thread.id}),
                    (message, {"folder_id": foreign_folder.id}), (link, {"thread_id": foreign_thread.id})]
        for record, vals in attempts:
            with self.subTest(model=record._name, vals=vals), self.assertRaises(AccessError), self.cr.savepoint():
                record.write(vals)
                self.env.flush_all()
        self.env.flush_all()
        self.assertEqual(self.env["umc.message"].search_count([("mailbox_id", "=", self.personal.id)]), 1)
        self.assertEqual(self.env["umc.thread"].search_count([("mailbox_id", "=", self.personal.id)]), 1)

    def test_signature_cannot_be_attached_to_another_mailbox(self):
        Signature = self.env["umc.signature"].with_user(self.colleague)
        mine = Signature.create({"name": "Me", "body": "<p>x</p>"})
        with self.assertRaises(AccessError), self.cr.savepoint():
            mine.write({"mailbox_id": self.personal.id})
        with self.assertRaises(AccessError), self.cr.savepoint():
            Signature.create({"name": "Planted", "body": "<p>x</p>", "mailbox_id": self.personal.id})
        own = make_mailbox(self.env, self.colleague, "Own", "own@lab0.test")
        shared = Signature.create({"name": "Shared", "body": "<p>x</p>", "owner_id": False, "mailbox_id": own.id})
        with self.assertRaises(AccessError), self.cr.savepoint():
            shared.write({"mailbox_id": self.team.id})
        mine.write({"mailbox_id": self.team.id})
        self.assertEqual(mine.mailbox_id, self.team)
