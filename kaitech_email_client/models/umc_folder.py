# -*- coding: utf-8 -*-
import base64
import re

from odoo import api, fields, models

ROLES = [("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("archive", "Archived"),
         ("spam", "Spam"), ("trash", "Trash"), ("other", "Folder")]

# RFC 6154 special-use flags, then common names (several languages) as a fallback.
SPECIAL_USE = {"\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\junk": "spam",
               "\\archive": "archive", "\\all": "other"}
NAME_ROLES = {
    "sent": "sent", "sent items": "sent", "sent mail": "sent", "sent messages": "sent", "enviados": "sent",
    "elementos enviados": "sent", "correo enviado": "sent", "envoyes": "sent",
    "drafts": "drafts", "draft": "drafts", "borradores": "drafts", "brouillons": "drafts",
    "trash": "trash", "deleted items": "trash", "deleted messages": "trash", "bin": "trash",
    "papelera": "trash", "elementos eliminados": "trash", "corbeille": "trash",
    "spam": "spam", "junk": "spam", "junk e-mail": "spam", "junk email": "spam", "correo no deseado": "spam",
    "archive": "archive", "archives": "archive", "archivo": "archive", "archivado": "archive",
}
LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


def decode_utf7(name):
    """IMAP modified UTF-7 (RFC 3501 5.1.3) -> str."""
    out, i = [], 0
    while i < len(name):
        if name[i] == "&":
            j = name.index("-", i)
            if j == i + 1:
                out.append("&")
            else:
                chunk = name[i + 1:j].replace(",", "/")
                out.append(base64.b64decode(chunk + "=" * (-len(chunk) % 4)).decode("utf-16-be"))
            i = j + 1
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


def parse_list_line(line):
    """b'(\\HasNoChildren \\Sent) "/" "Sent"' -> (flags, delimiter, raw_name) or None."""
    if isinstance(line, tuple):  # literal name: (b'(\\X) "/" {5}', b'Name')
        line = line[0].rsplit(b"{", 1)[0] + b'"' + line[1].replace(b'"', b'\\"') + b'"'
    m = LIST_RE.match(line.decode("utf-8", "replace"))
    if not m:
        return None
    name = m["name"].strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    delim = None if m["delim"] == "NIL" else m["delim"][1:-1]
    return [f.lower() for f in m["flags"].split()], delim, name


class UmcFolder(models.Model):
    _name = "umc.folder"
    _description = "Email Client for Odoo Full - folder"
    _order = "mailbox_id, sequence, name"

    mailbox_id = fields.Many2one("umc.mailbox", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, help="Display name")
    path = fields.Char(required=True, help="Raw IMAP name (modified UTF-7)")
    role = fields.Selection(ROLES, required=True, default="other")
    sequence = fields.Integer(default=50)
    sync = fields.Boolean("Synchronize", default=False)
    selectable = fields.Boolean(default=True)
    special_flags = fields.Char(help="RFC 6154 flags reported by the server (\\All, \\Archive...)")
    uidvalidity = fields.Char(readonly=True)
    last_uid = fields.Char(readonly=True, help="Highest UID already imported")
    unread_count = fields.Integer(readonly=True)
    total_count = fields.Integer(readonly=True)

    def init(self):
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS umc_folder_path_uniq ON umc_folder (mailbox_id, path)")

    @api.model
    def _role_for(self, flags, name, path):
        if path.upper() == "INBOX":
            return "inbox"
        for flag in flags:
            if flag in SPECIAL_USE:
                return SPECIAL_USE[flag]
        leaf = re.split(r"[/.]", name)[-1].strip().lower()
        return NAME_ROLES.get(leaf, "other")

    @api.model
    def _sequence_for(self, role):
        return {"inbox": 1, "sent": 2, "drafts": 3, "archive": 4, "spam": 5, "trash": 6}.get(role, 50)

    def write(self, vals):
        if self.env.su:
            return super().write(vals)
        self.mailbox_id._check_role("manage")
        res = super().write(vals)
        if "mailbox_id" in vals:
            self.mailbox_id._check_role("manage")
        return res

    def _refresh_counters(self):
        if not self:
            return
        self.env["umc.message"].flush_model(["folder_id", "is_read"])
        self.env.cr.execute("""
            SELECT folder_id, count(*), count(*) FILTER (WHERE NOT is_read)
              FROM umc_message WHERE folder_id IN %s GROUP BY folder_id
        """, [tuple(self.ids)])
        counts = {r[0]: r[1:] for r in self.env.cr.fetchall()}
        for folder in self:
            total, unread = counts.get(folder.id, (0, 0))
            if (folder.total_count, folder.unread_count) != (total, unread):
                folder.sudo().write({"total_count": total, "unread_count": unread})
