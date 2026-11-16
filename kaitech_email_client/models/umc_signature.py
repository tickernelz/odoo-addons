from lxml import html as lxml_html
from markupsafe import Markup

from odoo import api, fields, models

SIGNATURE_CLASS = "umc-signature"


class UmcSignature(models.Model):
    _name = "umc.signature"
    _description = "Email Client - signature"
    _order = "is_default desc, name, id"

    name = fields.Char(required=True)
    body = fields.Html(sanitize=True, required=True)
    owner_id = fields.Many2one("res.users", "Owner", default=lambda self: self.env.user, index=True,
                               ondelete="cascade", help="Empty for a signature shared on the mailbox.")
    mailbox_id = fields.Many2one("umc.mailbox", "Mailbox", index=True, ondelete="cascade",
                                 help="Only offered when writing from this mailbox. Empty: every mailbox.")
    is_default = fields.Boolean("Default", help="Used when the composer opens.")

    @api.model
    def _default_for(self, box):
        """Personal default for this mailbox, personal default, shared default of the mailbox, in that order."""
        uid = self.env.uid
        candidates = self.search([("is_default", "=", True), "|", ("mailbox_id", "=", False),
                                  ("mailbox_id", "=", box.id)])
        for test in (lambda s: s.owner_id.id == uid and s.mailbox_id == box,
                     lambda s: s.owner_id.id == uid and not s.mailbox_id,
                     lambda s: not s.owner_id and s.mailbox_id == box):
            found = candidates.filtered(test)[:1]
            if found:
                return found
        return self.browse()

    def write(self, vals):
        res = super().write(vals)
        self.check_access("write")
        return res


def signature_block(body):
    if not body:
        return Markup("")
    return Markup('<div class="%s">-- <br>%s</div>') % (SIGNATURE_CLASS, Markup(body))


def replace_signature(document, block):
    """Body with its signature block swapped for `block`; the block goes before the quote when there was none."""
    fragments = lxml_html.fragments_fromstring(str(document or "")) if (document or "").strip() else []
    root = lxml_html.Element("div")
    for frag in fragments:
        if isinstance(frag, str):
            if len(root):
                root[-1].tail = (root[-1].tail or "") + frag
            else:
                root.text = (root.text or "") + frag
        else:
            root.append(frag)
    old = root.find_class(SIGNATURE_CLASS)
    new = lxml_html.fragment_fromstring(str(block), create_parent="div") if block else None
    anchor = old[0] if old else (root.find_class("umc-quote") or [None])[0]
    position = root.index(anchor) if anchor is not None and anchor.getparent() is root else len(root)
    for node in old:
        tail = node.tail
        parent = node.getparent()
        prev = node.getprevious()
        parent.remove(node)
        if tail:
            if prev is not None:
                prev.tail = (prev.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
    if new is not None:
        for i, node in enumerate(list(new)):
            root.insert(position + i, node)
    inner = (root.text or "") + "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return Markup(inner)
