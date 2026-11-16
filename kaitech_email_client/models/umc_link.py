from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

LINK_MODELS = [
    "res.partner", "crm.lead", "project.project", "project.task", "sale.order", "purchase.order",
    "account.move", "stock.picking", "calendar.event",
]
CREATE_MODELS = ["crm.lead", "project.task"]


class UmcRecordLink(models.Model):
    _name = "umc.record.link"
    _description = "Email Client - conversation linked to an Odoo record"
    _order = "is_primary desc, id"

    thread_id = fields.Many2one("umc.thread", required=True, ondelete="cascade", index=True)
    mailbox_id = fields.Many2one(related="thread_id.mailbox_id", store=True, index=True)
    res_model = fields.Char("Model", required=True, index=True)
    res_id = fields.Many2oneReference("Record", model_field="res_model", required=True, index=True)
    is_primary = fields.Boolean("Primary", help="Replies sent from the inbox are logged on this record.")

    _record_uniq = models.Constraint("UNIQUE(thread_id, res_model, res_id)",
                                     "This conversation is already linked to that record.")

    def _card(self):
        record = self.env[self.res_model].browse(self.res_id).exists()
        return {
            "id": self.id, "model": self.res_model, "res_id": self.res_id, "primary": self.is_primary,
            "model_label": self.env["ir.model"]._get(self.res_model).name,
            "name": record.display_name if record else _("(deleted)"),
        }


class UmcThreadLinks(models.Model):
    _inherit = "umc.thread"

    link_ids = fields.One2many("umc.record.link", "thread_id", "Linked records")

    @api.model
    def umc_link_models(self):
        """Models the user may link a conversation to (installed, readable, with a chatter)."""
        out = []
        for name in LINK_MODELS:
            if name not in self.env:
                continue
            Model = self.env[name]
            if not hasattr(Model, "message_post") or not Model.has_access("read"):
                continue
            out.append({"model": name, "label": self.env["ir.model"]._get(name).name,
                        "create": name in CREATE_MODELS and Model.has_access("create")})
        return out

    def _check_link_model(self, model):
        if model not in {m["model"] for m in self.umc_link_models()}:
            raise UserError(_("Conversations cannot be linked to %s.", model))

    def _email_note(self, messages):
        """Chatter note carrying the emails: header lines, body and attachments."""
        parts = []
        for msg in messages:
            header = Markup("<p><b>%s</b><br/>%s: %s &lt;%s&gt;<br/>%s: %s<br/>%s: %s</p>") % (
                msg.subject or _("(no subject)"), _("From"), msg.from_name or "", msg.from_email or "",
                _("To"), msg.to or "", _("Date"), fields.Datetime.to_string(msg.date) if msg.date else "")
            parts.append(header + Markup(msg.body_html or ""))
        return Markup("<hr/>").join(parts)

    def _log_on_record(self, record, messages):
        attachments = self.env["ir.attachment"]
        for att in messages.attachment_ids.sudo().filtered(lambda a: a.description != "umc-inline"):
            attachments |= att.copy({"res_model": record._name, "res_id": record.id})
        record.message_post(body=self._email_note(messages), message_type="comment",
                            subtype_xmlid="mail.mt_note", attachment_ids=attachments.ids)

    @api.model
    def umc_link(self, thread_id, model, res_id, primary=False):
        thread = self.browse(int(thread_id)).exists()
        if not thread:
            raise UserError(_("This conversation no longer exists."))
        thread.check_access("read")
        thread.mailbox_id._check_role("reply")
        thread._check_link_model(model)
        record = self.env[model].browse(int(res_id)).exists()
        if not record:
            raise UserError(_("That record no longer exists."))
        record.check_access("write")
        Link = self.env["umc.record.link"].sudo()
        link = Link.search([("thread_id", "=", thread.id), ("res_model", "=", model), ("res_id", "=", record.id)])
        if not link:
            primary = primary or not thread.sudo().link_ids
            link = Link.create({"thread_id": thread.id, "res_model": model, "res_id": record.id})
            thread._log_on_record(record, thread.sudo().message_ids.sorted(lambda m: (m.date or m.create_date, m.id)))
        if primary:
            thread.sudo().link_ids.is_primary = False
            link.is_primary = True
        return thread.umc_links()

    @api.model
    def umc_unlink(self, link_id):
        link = self.env["umc.record.link"].sudo().browse(int(link_id)).exists()
        if not link:
            return []
        link.thread_id.check_access("read")
        link.mailbox_id._check_role("reply")
        thread = link.thread_id
        was_primary = link.is_primary
        link.unlink()
        if was_primary and thread.link_ids:
            thread.link_ids[0].is_primary = True
        return thread.umc_links()

    def umc_links(self):
        self.ensure_one()
        self.check_access("read")
        visible = []
        for link in self.sudo().link_ids:
            if link.res_model not in self.env:
                continue
            record = self.env[link.res_model].browse(link.res_id)
            if record.exists() and record.has_access("read"):
                visible.append(link._card())
        return visible

    @api.model
    def umc_create_record(self, thread_id, model, project_id=False):
        """Lead or task from the conversation, linked as primary."""
        thread = self.browse(int(thread_id)).exists()
        if not thread:
            raise UserError(_("This conversation no longer exists."))
        thread.check_access("read")
        thread.mailbox_id._check_role("reply")
        if model not in CREATE_MODELS:
            raise UserError(_("Records of %s cannot be created from an email.", model))
        thread._check_link_model(model)
        first = thread.sudo().message_ids.sorted(lambda m: (m.date or m.create_date, m.id))[:1]
        partner = thread.partner_id
        name = thread.subject or _("(no subject)")
        if model == "crm.lead":
            vals = {"name": name, "partner_id": partner.id or False, "email_from": first.from_email or False,
                    "contact_name": first.from_name or False, "description": first.body_html or False}
            if partner:
                vals["email_from"] = partner.email or vals["email_from"]
        else:
            project = self.env["project.project"].browse(int(project_id or 0)).exists()
            if not project:
                raise UserError(_("Choose the project of the task."))
            vals = {"name": name, "project_id": project.id, "partner_id": partner.id or False,
                    "description": first.body_html or False}
        try:
            record = self.env[model].create(vals)
        except AccessError:
            raise UserError(_("You are not allowed to create this record."))
        return {"links": thread.umc_link(thread.id, model, record.id, primary=True),
                "record": {"model": model, "res_id": record.id, "name": record.display_name}}
