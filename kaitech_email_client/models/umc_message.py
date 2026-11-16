# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import email_normalize, html2plaintext

LIST_FILTERS = ("all", "unread", "starred", "in", "out")
BODY_SEARCH_MIN = 3


class UmcThread(models.Model):
    """A conversation: every message of one mailbox that answers each other."""

    _name = "umc.thread"
    _description = "Email Client for Odoo Full - conversation"
    _order = "last_date desc, id desc"
    _rec_name = "subject"

    mailbox_id = fields.Many2one("umc.mailbox", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="mailbox_id.company_id", store=True)
    subject = fields.Char()
    normalized_subject = fields.Char(index=True)
    partner_id = fields.Many2one("res.partner", "Contact", index=True, ondelete="set null")
    participants = fields.Char(help="Every address seen in the conversation, comma separated")
    message_ids = fields.One2many("umc.message", "thread_id")
    folder_ids = fields.Many2many("umc.folder", "umc_thread_folder_rel", "thread_id", "folder_id")
    last_date = fields.Datetime(index=True)
    last_from = fields.Char()
    snippet = fields.Char()
    message_count = fields.Integer()
    unread_count = fields.Integer()
    is_starred = fields.Boolean()
    has_attachments = fields.Boolean()
    tag_ids = fields.Many2many("umc.tag", "umc_thread_tag_rel", "thread_id", "tag_id", "Tags")

    def _refresh_stats(self):
        for thread in self.exists():
            msgs = thread.message_ids.sorted(lambda m: (m.date or fields.Datetime.now(), m.id))
            last = msgs[-1:] if msgs else msgs
            emails = set()
            for m in msgs:
                emails.add(m.from_email)
                emails.update(e.strip() for e in (m.recipients or "").split(",") if e.strip())
            emails.discard(False)
            emails.discard("")
            thread.sudo().write({
                "message_count": len(msgs),
                "unread_count": len(msgs.filtered(lambda m: not m.is_read)),
                "is_starred": any(msgs.mapped("is_starred")),
                "has_attachments": any(msgs.mapped("has_attachments")),
                "last_date": last.date,
                "last_from": last.from_name or last.from_email,
                "snippet": last.snippet,
                "participants": ",".join(sorted(emails))[:2000],
                "folder_ids": [(6, 0, msgs.folder_id.ids)],
            })

    # ------------------------------------------------------------------ RPC for the inbox (OWL)
    @api.model
    def _inbox_domain(self, mailbox_id=False, role="inbox", folder_id=False, search="", unread_only=False,
                      partner_id=False, list_filter="all", tag_id=False):
        if tag_id:
            domain = [("tag_ids", "in", [int(tag_id)])]
        elif partner_id:
            domain = [("partner_id", "=", int(partner_id))]
        elif folder_id:
            domain = [("folder_ids", "in", [int(folder_id)])]
        else:
            fdom = [("role", "=", role or "inbox")]
            if mailbox_id:
                fdom.append(("mailbox_id", "=", int(mailbox_id)))
            domain = [("folder_ids", "in", self.env["umc.folder"].search(fdom).ids)]
        if search:
            fields_searched = ["subject", "participants", "snippet", "last_from"]
            if len(search) >= BODY_SEARCH_MIN:
                fields_searched.append("message_ids.body_text")
            domain += ["|"] * (len(fields_searched) - 1) + [(name, "ilike", search) for name in fields_searched]
        if unread_only or list_filter == "unread":
            domain.append(("unread_count", ">", 0))
        if list_filter == "starred":
            domain.append(("is_starred", "=", True))
        elif list_filter in ("in", "out"):
            domain.append(("message_ids.direction", "=", list_filter))
        return domain

    @api.model
    def umc_list(self, mailbox_id=False, role="inbox", folder_id=False, search="", unread_only=False,
                 offset=0, limit=40, partner_id=False, list_filter="all", tag_id=False):
        if list_filter not in LIST_FILTERS:
            raise UserError(_("Unknown filter."))
        domain = self._inbox_domain(mailbox_id, role, folder_id, (search or "").strip(), unread_only, partner_id,
                                    list_filter, tag_id)
        threads = self.search(domain, offset=offset, limit=limit)
        return {"total": self.search_count(domain), "threads": [t._umc_card() for t in threads]}

    def _umc_card(self):
        box = self.mailbox_id
        return {
            "id": self.id, "subject": self.subject or _("(no subject)"), "snippet": self.snippet or "",
            "date": fields.Datetime.to_string(self.last_date) if self.last_date else "",
            "from": self.last_from or "", "unread": self.unread_count, "count": self.message_count,
            "starred": self.is_starred, "attachments": self.has_attachments,
            "mailbox": {"id": box.id, "name": box.name, "color": box.color},
            "partner": {"id": self.partner_id.id, "name": self.partner_id.display_name} if self.partner_id else False,
            "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in self.tag_ids],
        }

    @api.model
    def umc_open(self, thread_id):
        thread = self.browse(int(thread_id)).exists()
        if not thread:
            raise UserError(_("This conversation no longer exists."))
        thread.check_access("read")
        unread = thread.message_ids.filtered(lambda m: not m.is_read)
        if unread:
            unread.sudo()._set_read(True)
        msgs = thread.message_ids.sorted(lambda m: (m.date or fields.Datetime.now(), m.id))
        box = thread.mailbox_id
        return {
            "thread": thread._umc_card(),
            "links": thread.umc_links(),
            "role": box.my_role,
            "messages": [m._umc_full() for m in msgs],
        }

    @api.model
    def umc_set_tags(self, thread_ids, tag_ids):
        threads = self.browse([int(i) for i in thread_ids]).exists()
        threads.check_access("read")
        threads.mailbox_id._check_role("reply")
        tags = self.env["umc.tag"].browse([int(i) for i in tag_ids]).exists()
        threads.sudo().tag_ids = [(6, 0, tags.ids)]
        return [{"id": t.id, "name": t.name, "color": t.color} for t in tags]

    @api.model
    def umc_tags(self):
        return [{"id": t.id, "name": t.name, "color": t.color} for t in self.env["umc.tag"].search([])]

    @api.model
    def umc_mark(self, thread_ids, action):
        threads = self.browse([int(i) for i in thread_ids]).exists()
        if not threads:
            return True
        messages = threads.message_ids
        if action in ("read", "unread"):
            threads.mailbox_id._check_role("read")
            messages.sudo()._set_read(action == "read")
        elif action in ("star", "unstar", "archive", "trash", "spam", "inbox"):
            threads.mailbox_id._check_role("reply")
            for box in threads.mailbox_id:
                box.sudo()._remote_action(messages.filtered(lambda m: m.mailbox_id == box), action)
        else:
            raise UserError(_("Unknown action."))
        return True


class UmcMessage(models.Model):
    _name = "umc.message"
    _description = "Email Client for Odoo Full - message"
    _order = "date desc, id desc"
    _rec_name = "subject"

    thread_id = fields.Many2one("umc.thread", required=True, ondelete="cascade", index=True)
    mailbox_id = fields.Many2one(related="thread_id.mailbox_id", store=True, index=True)
    folder_id = fields.Many2one("umc.folder", ondelete="set null", index=True)
    uid = fields.Char("IMAP UID", index=True)
    message_id = fields.Char("Message-ID", index=True)
    in_reply_to = fields.Char()
    references = fields.Text()
    subject = fields.Char()
    from_name = fields.Char()
    from_email = fields.Char(index=True)
    to = fields.Char()
    cc = fields.Char()
    reply_to = fields.Char()
    recipients = fields.Char(help="Lower-case addresses of To and Cc, comma separated")
    date = fields.Datetime(index=True)
    body_html = fields.Html(sanitize=False, help="Sanitized when the message is imported")
    body_text = fields.Text(compute="_compute_body_text", store=True, index="trigram",
                            help="Plain text of the body, searched by the inbox")
    snippet = fields.Char()
    direction = fields.Selection([("in", "Received"), ("out", "Sent")], default="in", required=True)
    is_read = fields.Boolean(default=False, index=True)
    is_starred = fields.Boolean(default=False)
    flags_pending = fields.Boolean(help="Read/unread changed in Odoo, not yet written to the server")
    partner_id = fields.Many2one("res.partner", "Contact", ondelete="set null", index=True)
    attachment_ids = fields.Many2many("ir.attachment", "umc_message_attachment_rel", "message_id", "attachment_id")
    has_attachments = fields.Boolean()

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS umc_message_folder_uid_uniq
            ON umc_message (folder_id, uid) WHERE uid IS NOT NULL AND folder_id IS NOT NULL
        """)

    @api.depends("body_html")
    def _compute_body_text(self):
        for message in self:
            message.body_text = " ".join(html2plaintext(message.body_html or "").split()) or False

    def _set_read(self, read):
        todo = self.filtered(lambda m: m.is_read != read)
        if not todo:
            return
        todo.write({"is_read": read, "flags_pending": True})
        todo.thread_id._refresh_stats()
        todo.folder_id._refresh_counters()
        todo.mailbox_id._refresh_unread()

    def _umc_full(self):
        return {
            "id": self.id, "from_name": self.from_name or "", "from_email": self.from_email or "",
            "to": self.to or "", "cc": self.cc or "", "subject": self.subject or "",
            "date": fields.Datetime.to_string(self.date) if self.date else "",
            "body": self.body_html or "", "direction": self.direction, "read": self.is_read,
            "starred": self.is_starred, "folder": self.folder_id.name or "",
            "partner": {"id": self.partner_id.id, "name": self.partner_id.display_name} if self.partner_id else False,
            "attachments": [{"id": a.id, "name": a.name, "mimetype": a.mimetype, "size": a.file_size}
                            for a in self.attachment_ids if not a.description == "umc-inline"],
        }

    @api.model
    def _match_partner(self, mailbox, address, name="", create=True):
        """Contact for an address: existing partner, or a new one if the mailbox allows it."""
        normalized = email_normalize(address or "")
        if not normalized:
            return self.env["res.partner"]
        own = {mailbox.email, mailbox.login, mailbox.oauth_email}
        if normalized in own:
            return self.env["res.partner"]
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("email_normalized", "=", normalized)], limit=1) \
            if "email_normalized" in Partner._fields else Partner.search([("email", "=ilike", normalized)], limit=1)
        if not partner and create and mailbox.auto_create_contacts:
            partner = Partner.create({"name": name or normalized, "email": normalized,
                                      "company_id": False})
        return partner
