# -*- coding: utf-8 -*-
"""IMAP synchronization engine.

Per mailbox and per synchronized folder:
  1. write read/unread changes made in Odoo back to the server (flags_pending)
  2. import new messages by UID (incremental; UIDVALIDITY change -> folder re-imported)
  3. refresh flags of recent messages and drop the ones deleted on the server
Bodies are fetched with BODY.PEEK[] so syncing never marks mail as read.
"""
import logging
import re
import threading
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import mail_parse
from .compat import raw_value

_logger = logging.getLogger(__name__)

FETCH_BATCH = 20
MAX_NEW_PER_RUN = 200        # per folder and run; the next run continues
MAX_INITIAL = 300            # first import of a folder: at most the last N messages...
FLAGS_WINDOW = 500           # flags refreshed for the last N local messages of a folder
CRON_BUDGET = 50             # seconds per cron run
UID_RE = re.compile(rb"UID (\d+)")
FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")


def q(path):
    return '"%s"' % path.replace("\\", "\\\\").replace('"', '\\"')


def _testing():
    return getattr(threading.current_thread(), "testing", False)


class UmcMailboxSync(models.Model):
    _inherit = "umc.mailbox"

    folder_ids = fields.One2many("umc.folder", "mailbox_id", "Folders")
    initial_days = fields.Integer("Import the last (days)", default=30,
                                  help="First synchronization: messages of the last N days (max 300 per folder).")
    auto_create_contacts = fields.Boolean("Create missing contacts", default=True,
                                          help="Create a contact for senders that are not in Odoo yet.")

    # ------------------------------------------------------------------ counters
    def _refresh_unread(self):
        for box in self:
            inbox = box.folder_ids.filtered(lambda f: f.role == "inbox")
            unread = sum(inbox.mapped("unread_count"))
            if box.unread_count != unread:
                box.sudo().unread_count = unread
        self._notify_counters()

    # ------------------------------------------------------------------ entry points
    @api.model
    def _cron_sync(self):
        deadline = time.time() + CRON_BUDGET
        for box in self.search([("sync_enabled", "=", True)]):
            if time.time() > deadline:
                break
            if not box._has_credentials():
                continue
            self.env.cr.execute("SELECT id FROM umc_mailbox WHERE id = %s FOR UPDATE SKIP LOCKED", [box.id])
            if not self.env.cr.fetchone():
                continue
            try:
                with self.env.cr.savepoint():
                    box._sync()
            except Exception as exc:
                _logger.warning("Email Client: sync of %s failed: %s", box.email, exc)
                box.write({"connection_state": "error", "last_error": str(exc)[:250]})
            if not _testing():
                self.env.cr.commit()

    def umc_sync_now(self):
        """'Sync' button of the inbox: every mailbox I can read (or the given ones)."""
        boxes = self or self.search([])
        boxes._check_role("read")
        errors, new = [], 0
        for box in boxes.sudo():
            try:
                if not box._has_credentials():
                    continue
                with self.env.cr.savepoint():
                    new += len(box._sync())
            except Exception as exc:
                box.write({"connection_state": "error", "last_error": str(exc)[:250]})
                errors.append("%s: %s" % (box.name, exc))
        return {"new": new, "errors": errors}

    def _has_credentials(self):
        self.ensure_one()
        secret = self._secret()
        return bool(secret.password) if self.account_type == "imap" else bool(secret.refresh_token)

    # ------------------------------------------------------------------ sync
    def _sync(self):
        """Synchronize this mailbox. Returns the new umc.message records."""
        self.ensure_one()
        box = self.sudo()
        imap = box._imap_connect()
        new = self.env["umc.message"]
        initial_folders = set()
        try:
            box._sync_folder_list(imap)
            for folder in box.folder_ids.filtered(lambda f: f.sync and f.selectable):
                if not folder.last_uid:
                    initial_folders.add(folder.id)
                new |= box._sync_folder(imap, folder)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        box.write({"connection_state": "connected", "last_error": False, "last_sync": fields.Datetime.now()})
        box.folder_ids._refresh_counters()
        box._refresh_unread()
        fresh = new.filtered(lambda m: m.direction == "in" and m.folder_id.role == "inbox"
                             and m.folder_id.id not in initial_folders and not m.is_read)
        if fresh:
            box._after_new_mail(fresh)
        return new

    def _sync_folder_list(self, imap):
        typ, data = imap.list()
        if typ != "OK":
            return
        Folder = self.env["umc.folder"].sudo()
        seen = set()
        existing = {f.path: f for f in self.folder_ids}
        for line in data or []:
            parsed = line and mail_parse_list(line)
            if not parsed:
                continue
            flags, _delim, path = parsed
            seen.add(path)
            name = decode_utf7_safe(path)
            selectable = "\\noselect" not in flags and "\\nonexistent" not in flags
            special = " ".join(f for f in flags if f in ("\\all", "\\archive", "\\sent", "\\drafts",
                                                          "\\trash", "\\junk", "\\flagged", "\\important"))
            if path in existing:
                folder = existing[path]
                if folder.selectable != selectable or folder.special_flags != special:
                    folder.write({"selectable": selectable, "special_flags": special})
                continue
            role = Folder._role_for(flags, name, path)
            Folder.create({"mailbox_id": self.id, "path": path, "name": "Inbox" if role == "inbox" else name,
                           "role": role, "sequence": Folder._sequence_for(role), "selectable": selectable,
                           "special_flags": special, "sync": role in ("inbox", "sent") and selectable})
        gone = self.folder_ids.filtered(lambda f: f.path not in seen)
        if gone:
            threads = self.env["umc.message"].sudo().search([("folder_id", "in", gone.ids)]).thread_id
            gone.unlink()
            threads._refresh_stats()

    def _sync_folder(self, imap, folder):
        typ, data = imap.select(q(folder.path))
        if typ != "OK":
            _logger.info("Email Client: cannot select %s on %s", folder.path, self.email)
            return self.env["umc.message"]
        uidvalidity = (imap.response("UIDVALIDITY")[1] or [b""])[0]
        uidvalidity = uidvalidity.decode() if isinstance(uidvalidity, bytes) else str(uidvalidity or "")
        Message = self.env["umc.message"].sudo()
        if folder.uidvalidity and uidvalidity and folder.uidvalidity != uidvalidity:
            stale = Message.search([("folder_id", "=", folder.id)])
            threads = stale.thread_id
            stale.unlink()
            threads._refresh_stats()
            folder.write({"last_uid": False})
        if folder.uidvalidity != uidvalidity:
            folder.write({"uidvalidity": uidvalidity})

        self._push_flags(imap, folder)
        new = self._fetch_new(imap, folder)
        self._refresh_flags(imap, folder)
        return new

    def _push_flags(self, imap, folder):
        pending = self.env["umc.message"].sudo().search(
            [("folder_id", "=", folder.id), ("flags_pending", "=", True), ("uid", "!=", False)])
        for read in (True, False):
            msgs = pending.filtered(lambda m: m.is_read == read)
            if msgs:
                imap.uid("STORE", ",".join(msgs.mapped("uid")), "+FLAGS" if read else "-FLAGS", "(\\Seen)")
        pending.write({"flags_pending": False})

    def _fetch_new(self, imap, folder):
        last = int(folder.last_uid or 0)
        if last:
            typ, data = imap.uid("SEARCH", None, "UID", "%d:*" % (last + 1))
        else:
            since = (fields.Date.today() - timedelta(days=max(self.initial_days or 30, 1))).strftime("%d-%b-%Y")
            typ, data = imap.uid("SEARCH", None, "SINCE", since)
        uids = sorted(int(u) for u in (data[0].split() if typ == "OK" and data and data[0] else []))
        uids = [u for u in uids if u > last]
        if not last:
            uids = uids[-MAX_INITIAL:]
        uids = uids[:MAX_NEW_PER_RUN]
        new = self.env["umc.message"]
        for i in range(0, len(uids), FETCH_BATCH):
            batch = uids[i:i + FETCH_BATCH]
            typ, data = imap.uid("FETCH", ",".join(map(str, batch)), "(UID FLAGS BODY.PEEK[])")
            if typ != "OK":
                break
            for uid, flags, raw in _iter_fetch(data):
                try:
                    with self.env.cr.savepoint():
                        new |= self._import_message(folder, uid, flags, raw)
                except Exception:
                    _logger.exception("Email Client: could not import UID %s of %s/%s", uid, self.email, folder.path)
            folder.write({"last_uid": str(batch[-1])})
            if not _testing() and self.env.context.get("umc_commit_batches"):
                self.env.cr.commit()
        new.thread_id._refresh_stats()
        return new

    def _refresh_flags(self, imap, folder):
        local = self.env["umc.message"].sudo().search(
            [("folder_id", "=", folder.id), ("uid", "!=", False)], order="id desc", limit=FLAGS_WINDOW)
        if not local:
            return
        by_uid = {int(m.uid): m for m in local}
        typ, data = imap.uid("FETCH", "%d:%d" % (min(by_uid), max(by_uid)), "(UID FLAGS)")
        if typ != "OK":
            return
        on_server = {}
        for item in data or []:
            line = item[0] if isinstance(item, tuple) else item
            if not isinstance(line, bytes):
                continue
            uid, flags = UID_RE.search(line), FLAGS_RE.search(line)
            if uid:
                on_server[int(uid.group(1))] = (flags.group(1) if flags else b"").lower()
        changed = self.env["umc.thread"]
        for uid, msg in by_uid.items():
            if uid not in on_server:
                changed |= msg.thread_id
                msg.unlink()
                continue
            flags = on_server[uid]
            vals = {}
            if not msg.flags_pending and (b"\\seen" in flags) != msg.is_read:
                vals["is_read"] = b"\\seen" in flags
            if (b"\\flagged" in flags) != msg.is_starred:
                vals["is_starred"] = b"\\flagged" in flags
            if vals:
                msg.write(vals)
                changed |= msg.thread_id
        empty = changed.exists().filtered(lambda t: not t.message_ids)
        empty.unlink()
        (changed.exists())._refresh_stats()

    # ------------------------------------------------------------------ import one message
    def _import_message(self, folder, uid, flags, raw):
        Message = self.env["umc.message"].sudo()
        uid = str(uid)
        if Message.search_count([("folder_id", "=", folder.id), ("uid", "=", uid)]):
            return Message
        p = mail_parse.parse(raw)
        flags = (flags or b"").lower()
        is_read = b"\\seen" in flags
        is_starred = b"\\flagged" in flags
        if p["message_id"]:  # sent from Odoo (or moved) and waiting for its UID
            waiting = Message.search([("folder_id", "=", folder.id), ("message_id", "=", p["message_id"]),
                                      ("uid", "=", False)], limit=1)
            if waiting:
                waiting.write({"uid": uid, "is_read": is_read or waiting.is_read, "is_starred": is_starred})
                return Message
        own = {a for a in (self.email, self.login, self.oauth_email) if a}
        outgoing = folder.role in ("sent", "drafts") or p["from_email"] in own
        thread = self._find_thread(p)
        if outgoing:
            first = (p["to"] or p["cc"] or [("", "")])[0]
            partner = Message._match_partner(self, first[1], first[0], create=folder.role == "sent")
        else:
            partner = Message._match_partner(self, p["from_email"], p["from_name"])
        recipients = [e for _n, e in p["to"] + p["cc"]]
        msg = Message.create({
            "thread_id": thread.id, "folder_id": folder.id, "uid": uid,
            "message_id": p["message_id"] or False, "in_reply_to": p["in_reply_to"] or False,
            "references": p["references"] or False, "subject": p["subject"][:500],
            "from_name": p["from_name"][:250], "from_email": p["from_email"][:250],
            "to": mail_parse.format_addresses(p["to"])[:2000], "cc": mail_parse.format_addresses(p["cc"])[:2000],
            "reply_to": mail_parse.format_addresses(p["reply_to"])[:500],
            "recipients": ",".join(recipients)[:2000], "date": p["date"] or fields.Datetime.now(),
            "body_html": p["body_html"], "snippet": p["snippet"],
            "direction": "out" if outgoing else "in", "is_read": is_read or outgoing, "is_starred": is_starred,
            "partner_id": partner.id or False, "has_attachments": any(not a["inline"] for a in p["attachments"]),
        })
        if p["attachments"]:
            msg._store_attachments(p["attachments"])
        if partner and (outgoing or not thread.partner_id or thread.partner_id != partner):
            thread.partner_id = partner
        return msg

    def _find_thread(self, p):
        Thread = self.env["umc.thread"].sudo()
        refs = [r for r in [p["in_reply_to"]] + (p["references"] or "").split() if r]
        if refs:
            parent = self.env["umc.message"].sudo().search(
                [("mailbox_id", "=", self.id), ("message_id", "in", refs)], order="date desc", limit=1)
            if parent:
                return parent.thread_id
        norm = mail_parse.normalize_subject(p["subject"])
        if norm and refs:
            since = (p["date"] or fields.Datetime.now()) - timedelta(days=30)
            people = {p["from_email"]} | {e for _n, e in p["to"] + p["cc"]}
            for cand in Thread.search([("mailbox_id", "=", self.id), ("normalized_subject", "=", norm),
                                       ("last_date", ">=", since)], order="last_date desc", limit=5):
                if people & set((cand.participants or "").split(",")):
                    return cand
        return Thread.create({"mailbox_id": self.id, "subject": p["subject"][:500] or False,
                              "normalized_subject": norm or False})

    # ------------------------------------------------------------------ after import
    def _after_new_mail(self, messages):
        """Live notification for newly received mail."""
        last = messages.sorted("date")[-1:]
        payload = {"mailbox_id": self.id, "mailbox": self.name, "count": len(messages),
                   "thread_id": last.thread_id.id, "from": last.from_name or last.from_email,
                   "subject": last.subject or ""}
        for user in self.user_ids:
            user._bus_send("umc.new_mail", payload)

    # ------------------------------------------------------------------ actions written to the server
    def _folder_for(self, action, imap=None):
        self.ensure_one()
        folders = self.folder_ids
        if action == "inbox":
            return folders.filtered(lambda f: f.role == "inbox")[:1]
        role = {"archive": "archive", "trash": "trash", "spam": "spam"}[action]
        target = folders.filtered(lambda f: f.role == role and f.selectable)[:1]
        if not target and action == "archive":
            target = folders.filtered(lambda f: "\\all" in (f.special_flags or ""))[:1]
        if not target and imap is not None:
            name = {"archive": "Archive", "trash": "Trash", "spam": "Junk"}[action]
            imap.create(q(name))
            self._sync_folder_list(imap)
            target = self.folder_ids.filtered(lambda f: f.path == name)[:1]
            target.write({"role": role, "sequence": self.env["umc.folder"]._sequence_for(role)})
        if not target:
            raise UserError(_("No %s folder on the server.", role))
        return target

    def _remote_action(self, messages, action):
        """Star, archive, delete... on the server first, then in Odoo (two_way_sync)."""
        self.ensure_one()
        messages = messages.sudo().filtered("uid")
        if not messages:
            return
        imap = self._imap_connect()
        try:
            typ, data = imap.capability()  # capabilities change after login (MOVE, UIDPLUS...)
            caps = set((data[0] or b"").decode().upper().split()) if typ == "OK" and data else set()
            caps |= {c.upper() for c in (imap.capabilities or ())}
            for folder in messages.folder_id:
                msgs = messages.filtered(lambda m: m.folder_id == folder)
                uidset = ",".join(msgs.mapped("uid"))
                if imap.select(q(folder.path))[0] != "OK":
                    continue
                if action in ("star", "unstar"):
                    imap.uid("STORE", uidset, "+FLAGS" if action == "star" else "-FLAGS", "(\\Flagged)")
                    msgs.write({"is_starred": action == "star"})
                    continue
                dest = self._folder_for(action, imap)
                if dest == folder:
                    continue
                imap.select(q(folder.path))
                if "MOVE" in caps:
                    typ, resp = imap.uid("MOVE", uidset, q(dest.path))
                else:
                    typ, resp = imap.uid("COPY", uidset, q(dest.path))
                    if typ == "OK":
                        imap.uid("STORE", uidset, "+FLAGS", "(\\Deleted)")
                        if "UIDPLUS" in caps:
                            imap.uid("EXPUNGE", uidset)
                        else:
                            imap.expunge()
                if typ != "OK":
                    raise UserError(_("The server refused the operation: %s", resp))
                msgs.write({"folder_id": dest.id, "uid": False})
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        messages.thread_id._refresh_stats()
        self.folder_ids._refresh_counters()
        self._refresh_unread()


def mail_parse_list(line):
    from .umc_folder import parse_list_line
    return parse_list_line(line)


def decode_utf7_safe(path):
    from .umc_folder import decode_utf7
    try:
        return decode_utf7(path)
    except Exception:
        return path


def _iter_fetch(data):
    """Yield (uid, flags_bytes, raw_message) from an imaplib UID FETCH response."""
    i = 0
    while i < len(data):
        item = data[i]
        if isinstance(item, tuple):
            meta = item[0]
            raw = item[1]
            # some servers send FLAGS after the literal, in the next plain bytes element
            if i + 1 < len(data) and isinstance(data[i + 1], bytes):
                meta += data[i + 1]
            uid = UID_RE.search(meta)
            flags = FLAGS_RE.search(meta)
            if uid:
                yield int(uid.group(1)), flags.group(1) if flags else b"", raw
        i += 1


class UmcMessageStore(models.Model):
    _inherit = "umc.message"

    def _store_attachments(self, attachments):
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        body = self.body_html or ""
        records = Attachment
        for att in attachments:
            rec = Attachment.create({
                "name": (att["name"] or "attachment")[:250], "mimetype": att["mimetype"],
                "raw": raw_value(att["data"]), "res_model": "umc.message", "res_id": self.id,
                "description": "umc-inline" if att["inline"] else False,
            })
            records |= rec
            if att["content_id"]:
                body = body.replace("cid:%s" % att["content_id"], "/web/image/%d" % rec.id)
        vals = {"attachment_ids": [(6, 0, records.ids)]}
        if body != (self.body_html or ""):
            vals["body_html"] = body
        self.write(vals)
