# -*- coding: utf-8 -*-
import imaplib
import logging
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from lxml import html as lxml_html
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import email_split, email_split_and_format, html2plaintext

from .compat import attachment_bytes, raw_value
from .mail_parse import normalize_subject
from .umc_link import LINK_MODELS
from .umc_signature import replace_signature, signature_block
from .umc_sync import q

_logger = logging.getLogger(__name__)


def _display_address(name, address):
    """'Name <address>' as typed by a person (formataddr would RFC 2047-encode accents)."""
    if not address:
        return ""
    name = (name or "").replace('"', "").strip()
    return "%s <%s>" % (name, address) if name and name != address else address


class UmcComposer(models.TransientModel):
    """New message, reply, reply all and forward from any mailbox the user can send from."""

    _name = "umc.composer"
    _description = "Email Client for Odoo Full - composer"

    mailbox_id = fields.Many2one("umc.mailbox", "From", required=True,
                                 domain="[('id', 'in', allowed_mailbox_ids)]")
    allowed_mailbox_ids = fields.Many2many("umc.mailbox", compute="_compute_allowed_mailbox_ids")
    mode = fields.Selection([("new", "New"), ("reply", "Reply"), ("reply_all", "Reply all"),
                             ("forward", "Forward")], default="new", required=True)
    parent_id = fields.Many2one("umc.message", "In reply to", ondelete="cascade")
    to_emails = fields.Char("To", help="Comma separated")
    cc_emails = fields.Char("Cc")
    bcc_emails = fields.Char("Bcc")
    show_cc = fields.Boolean("Cc / Bcc")
    subject = fields.Char()
    body = fields.Html()
    attachment_ids = fields.Many2many("ir.attachment", "umc_composer_attachment_rel", "composer_id",
                                      "attachment_id", "Attachments")
    signature_id = fields.Many2one("umc.signature", "Signature",
                                   domain="['|', ('mailbox_id', '=', False), ('mailbox_id', '=', mailbox_id)]")
    template_id = fields.Many2one("mail.template", "Template",
                                  domain="['|', '|', ('model', '=', False), ('model', '=', 'res.partner'), "
                                         "('model', '=', link_model)]")
    link_model = fields.Char(compute="_compute_link_model")
    link_ref = fields.Reference(selection="_link_selection", string="Log on record",
                                help="The sent message is also logged in the chatter of this record.")

    @api.depends("link_ref")
    def _compute_link_model(self):
        for rec in self:
            rec.link_model = rec.link_ref._name if rec.link_ref else False

    @api.model
    def _link_selection(self):
        return [(m["model"], m["label"]) for m in self.env["umc.thread"].umc_link_models()]

    @api.depends_context("uid")
    def _compute_allowed_mailbox_ids(self):
        boxes = self.env["umc.mailbox"].search([]).filtered(lambda b: b.my_role in ("reply", "manage"))
        for rec in self:
            rec.allowed_mailbox_ids = boxes

    # ------------------------------------------------------------------ defaults
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        Mailbox = self.env["umc.mailbox"]
        allowed = Mailbox.search([]).filtered(lambda b: b.my_role in ("reply", "manage"))
        parent = self.env["umc.message"].browse(ctx.get("umc_message_id")).exists()
        mode = ctx.get("umc_mode") or ("reply" if parent else "new")
        box = parent.mailbox_id if parent else Mailbox.browse(ctx.get("umc_mailbox_id")).exists()
        if box not in allowed:
            box = allowed[:1]
        res.update({"mode": mode, "mailbox_id": box.id or False, "parent_id": parent.id or False})
        if ctx.get("umc_to"):
            res["to_emails"] = ctx["umc_to"]
        chosen = self.env["umc.signature"]._default_for(box)
        if chosen:
            res["signature_id"] = chosen.id
        signature = self._signature_html(box, chosen)
        primary = parent.thread_id.sudo().link_ids.filtered("is_primary")[:1] if parent else False
        if primary and mode != "forward" and primary.res_model in self.env:
            record = self.env[primary.res_model].browse(primary.res_id).exists()
            if record and record.has_access("read"):
                res["link_ref"] = "%s,%s" % (record._name, record.id)
        if not parent:
            res["body"] = Markup("<p><br></p>") + signature
            return res

        own = {a for a in (box.email, box.login, box.oauth_email) if a}
        subject = parent.subject or ""
        if mode == "forward":
            res["subject"] = subject if subject.lower().startswith(("fwd:", "fw:")) else "Fwd: " + subject
            quote = Markup(
                '<div class="umc-quote"><p>---------- %s ----------<br>%s: %s &lt;%s&gt;<br>%s: %s<br>%s: %s<br>'
                '%s: %s</p>%s</div>') % (
                _("Forwarded message"), _("From"), parent.from_name or "", parent.from_email or "",
                _("Date"), self._local_date(parent.date), _("Subject"), subject, _("To"), parent.to or "",
                Markup(parent.body_html or ""))
            res["body"] = Markup("<p><br></p>") + signature + Markup("<br>") + quote
            copies = self.env["ir.attachment"]
            for att in parent.attachment_ids.filtered(lambda a: a.description != "umc-inline"):
                copies |= att.sudo().copy({"res_model": "umc.composer", "res_id": 0})
            res["attachment_ids"] = [(6, 0, copies.ids)]
            return res

        res["subject"] = subject if subject.lower().startswith("re:") else "Re: " + subject
        if parent.direction == "out":
            to = parent.to or ""
        else:
            to = parent.reply_to or _display_address(parent.from_name, parent.from_email)
        res["to_emails"] = to
        if mode == "reply_all":
            main = set(email_split(to))
            cc = [a for a in email_split_and_format(", ".join(filter(None, [parent.to, parent.cc])))
                  if not set(email_split(a)) & (main | own)]
            if cc:
                res.update({"cc_emails": ", ".join(cc), "show_cc": True})
        header = _("On %(date)s, %(who)s wrote:", date=self._local_date(parent.date),
                   who=parent.from_name or parent.from_email)
        quote = Markup('<div class="umc-quote"><p>%s</p><blockquote style="margin:0 0 0 .8ex;border-left:2px solid #ccc;'
                       'padding-left:1ex">%s</blockquote></div>') % (header, Markup(parent.body_html or ""))
        res["body"] = Markup("<p><br></p>") + signature + Markup("<br>") + quote
        return res

    @api.model
    def _local_date(self, value):
        if not value:
            return ""
        return fields.Datetime.context_timestamp(self, value).strftime("%Y-%m-%d %H:%M")

    @api.model
    def _signature_html(self, box, signature=False):
        if signature:
            return signature_block(signature.body)
        return signature_block(box.signature if box else False)

    @api.onchange("signature_id")
    def _onchange_signature_id(self):
        self.body = replace_signature(self.body, self._signature_html(self.mailbox_id, self.signature_id))

    @api.onchange("template_id")
    def _onchange_template_id(self):
        template = self.template_id
        if not template:
            return
        record = self._template_record(template)
        if template.model and not record:
            self.template_id = False
            return {"warning": {"title": _("Template not applied"), "message": _(
                "This template fills in data from a %s; choose one under 'Log on record' first.",
                template.model_id.name)}}
        rendered = {}
        for field in ("subject", "body_html"):
            if record:
                rendered[field] = template._render_field(field, record.ids)[record.id]
            else:
                rendered[field] = template[field] if field == "subject" else Markup(template[field] or "")
        if rendered["subject"]:
            self.subject = rendered["subject"]
        intro = Markup(rendered["body_html"] or "")
        tail = replace_signature("", self._signature_html(self.mailbox_id, self.signature_id))
        quote = self._quote_of(self.body)
        self.body = intro + tail + quote
        if template.attachment_ids:
            copies = self.env["ir.attachment"]
            for att in template.attachment_ids.sudo():
                copies |= att.copy({"res_model": "umc.composer", "res_id": 0})
            self.attachment_ids = [(4, a.id) for a in copies]

    def _template_record(self, template):
        """Record the template renders on: the record to log on, else the contact of the conversation."""
        if not template.model:
            return False
        if self.link_ref and self.link_ref._name == template.model:
            return self.link_ref
        if template.model == "res.partner":
            parent = self.parent_id
            partner = parent.partner_id or parent.thread_id.partner_id if parent else False
            if not partner and self.to_emails:
                first = email_split(self.to_emails)[:1]
                partner = self.env["res.partner"].search([("email_normalized", "=", first[0])], limit=1) if first else False
            return partner or False
        return False

    @api.model
    def _quote_of(self, body):
        text = str(body or "")
        if "umc-quote" not in text:
            return Markup("")
        root = lxml_html.fragment_fromstring(text, create_parent="div")
        quotes = root.find_class("umc-quote")
        return Markup("".join(lxml_html.tostring(q, encoding="unicode", with_tail=False) for q in quotes[:1]))

    # ------------------------------------------------------------------ send
    def action_send(self):
        self.ensure_one()
        box = self.mailbox_id
        box._check_role("reply")
        to = email_split_and_format(self.to_emails or "")
        cc = email_split_and_format(self.cc_emails or "")
        bcc = email_split_and_format(self.bcc_emails or "")
        if not (to or cc or bcc):
            raise UserError(_("Add at least one recipient."))
        bad = [a for a in (self.to_emails or "", self.cc_emails or "", self.bcc_emails or "")
               if a.strip() and not email_split(a)]
        if bad:
            raise UserError(_("Invalid address: %s", bad[0]))

        msg = self._build_message(to, cc)
        envelope = email_split(", ".join(to + cc + bcc))
        sbox = box.sudo()
        smtp = sbox._smtp_connect()
        try:
            smtp.send_message(msg, from_addr=box.login if "@" in (box.login or "") else box.email,
                              to_addrs=envelope)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        raw = msg.as_bytes()
        sent = sbox.folder_ids.filtered(lambda f: f.role == "sent")[:1]
        if sent and box.account_type == "imap":  # Gmail and Microsoft 365 file sent mail themselves
            try:
                imap = sbox._imap_connect()
                try:
                    imap.append(q(sent.path), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
                finally:
                    imap.logout()
            except Exception as exc:
                _logger.warning("Email Client: could not save the sent copy in %s: %s", sent.path, exc)
        rec = self._store_sent(msg, sent, to, cc)
        self._log_sent_on_record(rec)
        return {"type": "ir.actions.act_window_close"}

    def _log_sent_on_record(self, message):
        record = self.link_ref
        if not record or record._name not in LINK_MODELS:
            return
        thread = message.thread_id
        self.env["umc.thread"]._check_link_model(record._name)
        record.check_access("write")
        Link = self.env["umc.record.link"].sudo()
        if not Link.search_count([("thread_id", "=", thread.id), ("res_model", "=", record._name),
                                  ("res_id", "=", record.id)]):
            Link.create({"thread_id": thread.id, "res_model": record._name, "res_id": record.id,
                         "is_primary": not thread.link_ids})
        thread._log_on_record(record, message)

    def _build_message(self, to, cc):
        box = self.mailbox_id
        sender_name = self.env.user.name if box.mailbox_type == "personal" else box.name
        msg = EmailMessage()
        msg["From"] = formataddr((sender_name, box.email))
        if to:
            msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = self.subject or ""
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=box.email.split("@")[-1])
        parent = self.parent_id
        if parent and parent.message_id and self.mode != "forward":
            msg["In-Reply-To"] = parent.message_id
            msg["References"] = " ".join(filter(None, [parent.references, parent.message_id]))
        html = str(self.body or "")
        msg.set_content(html2plaintext(html) or " ")
        msg.add_alternative("<html><body>%s</body></html>" % html, subtype="html")
        for att in self.attachment_ids:
            maintype, _sep, subtype = (att.mimetype or "application/octet-stream").partition("/")
            msg.add_attachment(attachment_bytes(att.sudo()), maintype=maintype,
                               subtype=subtype or "octet-stream", filename=att.name)
        return msg

    def _store_sent(self, msg, sent_folder, to, cc):
        """Show the message in the conversation right away; the next sync only adds its UID."""
        box = self.mailbox_id.sudo()
        Message = self.env["umc.message"].sudo()
        thread = self.parent_id.thread_id if self.parent_id and self.mode != "forward" else False
        if not thread:
            thread = self.env["umc.thread"].sudo().create({
                "mailbox_id": box.id, "subject": self.subject, "normalized_subject": normalize_subject(self.subject)})
        first = email_split(", ".join(to + cc))[:1]
        partner = Message._match_partner(box, first[0] if first else "", "", create=True)
        html = str(self.body or "")
        rec = Message.create({
            "thread_id": thread.id, "folder_id": sent_folder.id if sent_folder else False, "uid": False,
            "message_id": msg["Message-ID"], "in_reply_to": msg["In-Reply-To"] or False,
            "references": msg["References"] or False, "subject": self.subject,
            "from_name": box.name if box.mailbox_type == "team" else self.env.user.name,
            "from_email": box.email, "to": ", ".join(to), "cc": ", ".join(cc),
            "recipients": ",".join(email_split(", ".join(to + cc))), "date": fields.Datetime.now(),
            "body_html": html, "snippet": " ".join(html2plaintext(html).split())[:200],
            "direction": "out", "is_read": True, "partner_id": partner.id or False,
            "has_attachments": bool(self.attachment_ids),
        })
        if self.attachment_ids:
            copies = self.env["ir.attachment"]
            for att in self.attachment_ids.sudo():
                copies |= self.env["ir.attachment"].sudo().create({
                    "name": att.name, "mimetype": att.mimetype, "raw": raw_value(attachment_bytes(att)),
                    "res_model": "umc.message", "res_id": rec.id})
            rec.attachment_ids = [(6, 0, copies.ids)]
        if partner and not thread.partner_id:
            thread.partner_id = partner
        thread._refresh_stats()
        if sent_folder:
            sent_folder._refresh_counters()
        return rec
