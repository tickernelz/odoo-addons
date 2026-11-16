# -*- coding: utf-8 -*-
import hashlib
import hmac
import imaplib
import logging
import smtplib
import ssl
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import oauth_providers as oauth
from .compat import get_param

_logger = logging.getLogger(__name__)

ACCOUNT_TYPES = [
    ("imap", "IMAP / SMTP"),
    ("google", "Gmail / Google Workspace"),
    ("microsoft", "Microsoft 365 / Outlook"),
]
SECURITY = [("ssl", "SSL/TLS"), ("starttls", "STARTTLS"), ("none", "None")]
ROLES = [("read", "Read"), ("reply", "Read and reply"), ("manage", "Manage")]
ROLE_RANK = {"read": 1, "reply": 2, "manage": 3}
OAUTH_STATE_MAX_AGE = 900  # seconds


class UmcMailbox(models.Model):
    """A connected mailbox: personal (one user) or team (ventas@ shared by N users).

    Who can do what is decided here, not in the UI:
      * read    -> see the mailbox and its conversations
      * reply   -> also send from it
      * manage  -> also edit settings, credentials and members
    The owner and administrators always have ``manage``. Credentials live in
    ``umc.mailbox.secret`` and never leave the server.
    """

    _name = "umc.mailbox"
    _description = "Email Client for Odoo Full - mailbox"
    _order = "sequence, name, id"

    name = fields.Char(required=True, help="Shown in the unified inbox, e.g. 'Sales'")
    email = fields.Char("Email address", required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    account_type = fields.Selection(ACCOUNT_TYPES, "Account", required=True, default="imap")
    mailbox_type = fields.Selection([("personal", "Personal"), ("team", "Team")], "Type",
                                    required=True, default="personal")

    # ---- Access ---------------------------------------------------------
    owner_id = fields.Many2one("res.users", "Owner", required=True, default=lambda self: self.env.user,
                               index=True, ondelete="restrict")
    member_ids = fields.One2many("umc.mailbox.member", "mailbox_id", "Team members", copy=False)
    user_ids = fields.Many2many(
        "res.users", "umc_mailbox_res_users_rel", "mailbox_id", "user_id", string="Users with access",
        compute="_compute_user_ids", store=True,
        help="Owner + team members. Drives record rules, counters and notifications.")
    my_role = fields.Selection(ROLES, "My access", compute="_compute_my_role")

    # ---- Servers ----------------------------------------------------------
    imap_host = fields.Char("IMAP server")
    imap_port = fields.Integer("IMAP port", default=993)
    imap_security = fields.Selection(SECURITY, "IMAP security", default="ssl")
    smtp_host = fields.Char("SMTP server")
    smtp_port = fields.Integer("SMTP port", default=587)
    smtp_security = fields.Selection(SECURITY, "SMTP security", default="starttls")
    login = fields.Char("Username", help="Usually the email address")
    password = fields.Char(compute="_compute_password", inverse="_inverse_password",
                           help="Stored apart, never sent back to the browser.")
    has_password = fields.Boolean(compute="_compute_password")

    # ---- OAuth2 / state ---------------------------------------------------
    oauth_email = fields.Char("Signed in as", readonly=True)
    connection_state = fields.Selection(
        [("draft", "Not connected"), ("connected", "Connected"), ("error", "Error")],
        default="draft", readonly=True, required=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)

    # ---- Inbox -----------------------------------------------------------
    signature = fields.Html(sanitize=True, help="Added to every message sent from this mailbox.")
    sync_enabled = fields.Boolean("Synchronize", default=True)
    last_sync = fields.Datetime(readonly=True, copy=False)
    unread_count = fields.Integer("Unread", readonly=True, copy=False)

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS umc_mailbox_email_company_uniq
            ON umc_mailbox (company_id, lower(email)) WHERE active
        """)

    # ------------------------------------------------------------------ computes
    @api.depends("owner_id", "member_ids.user_id")
    def _compute_user_ids(self):
        for box in self:
            box.user_ids = box.owner_id | box.member_ids.user_id

    @api.depends_context("uid")
    @api.depends("owner_id", "member_ids.role", "member_ids.user_id")
    def _compute_my_role(self):
        for box in self:
            box.my_role = box._role_of(self.env.user)

    def _compute_password(self):
        secrets = self._secrets()
        for box in self:
            box.password = False
            box.has_password = bool(secrets.get(box.id) and secrets[box.id].password)

    def _inverse_password(self):
        for box in self:
            if box.password:  # an empty field in the form means "keep the current one"
                box._secret().write({"password": box.password})

    def _secrets(self):
        recs = self.env["umc.mailbox.secret"].sudo().search([("mailbox_id", "in", self.ids)])
        return {s.mailbox_id.id: s for s in recs}

    def _secret(self):
        self.ensure_one()
        Secret = self.env["umc.mailbox.secret"].sudo()
        return Secret.search([("mailbox_id", "=", self.id)], limit=1) or Secret.create({"mailbox_id": self.id})

    # ------------------------------------------------------------------ permissions
    def _role_of(self, user):
        self.ensure_one()
        if user == self.owner_id or user.has_group("base.group_system"):
            return "manage"
        member = self.member_ids.filtered(lambda m: m.user_id == user)
        return member[:1].role or False

    def _check_role(self, role):
        if self.env.su:
            return True
        for box in self:
            if ROLE_RANK.get(box._role_of(self.env.user) or "", 0) < ROLE_RANK[role]:
                raise AccessError(_("You need '%(role)s' access on mailbox %(box)s.",
                                    role=dict(ROLES)[role], box=box.display_name))
        return True

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("email"):
                vals["email"] = vals["email"].strip().lower()
            vals.setdefault("login", vals.get("email"))
        return super().create(vals_list)

    def write(self, vals):
        self._check_role("manage")
        if vals.get("email"):
            vals["email"] = vals["email"].strip().lower()
        return super().write(vals)

    def unlink(self):
        self._check_role("manage")
        messages = self.env["umc.message"].sudo().search([("mailbox_id", "in", self.ids)])
        self.env["ir.attachment"].sudo().search(
            [("res_model", "=", "umc.message"), ("res_id", "in", messages.ids)]).unlink()
        return super().unlink()

    @api.onchange("account_type")
    def _onchange_account_type(self):
        conf = oauth.PROVIDERS.get(self.account_type)
        if conf:
            self.imap_host, self.imap_port, self.imap_security = conf["imap"]
            self.smtp_host, self.smtp_port, self.smtp_security = conf["smtp"]

    @api.onchange("email")
    def _onchange_email(self):
        if self.email and not self.login:
            self.login = self.email.strip().lower()

    # ------------------------------------------------------------------ OAuth2
    def _oauth_state(self):
        ts = str(int(time.time()))
        msg = "%s.%s.%s" % (self.id, self.env.uid, ts)
        return msg + "." + self._sign(msg)

    def _sign(self, msg):
        secret = get_param(self.env, "database.secret")
        return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]

    @api.model
    def _from_oauth_state(self, state):
        """Mailbox and user encoded in a state we signed, or raise AccessError."""
        try:
            box_id, uid, ts, sig = state.split(".")
        except (AttributeError, ValueError):
            raise AccessError(_("Invalid OAuth state."))
        msg = "%s.%s.%s" % (box_id, uid, ts)
        if not hmac.compare_digest(sig, self._sign(msg)) or time.time() - int(ts) > OAUTH_STATE_MAX_AGE:
            raise AccessError(_("The sign-in link expired. Start again from the mailbox."))
        return self.browse(int(box_id)).exists(), int(uid)

    def action_connect_oauth(self):
        self.ensure_one()
        self._check_role("manage")
        try:
            url = oauth.authorization_url(self.env, self.account_type, self._oauth_state(),
                                          login_hint=self.email)
        except oauth.OAuthError:
            raise UserError(_("Configure the %s application first: Settings > Email Client > Gmail and Microsoft 365.",
                              dict(ACCOUNT_TYPES)[self.account_type]))
        return {"type": "ir.actions.act_url", "url": url, "target": "self"}

    def _store_tokens(self, tokens):
        self.ensure_one()
        vals = {"access_token": tokens["access_token"], "token_expires_at": tokens["expires_at"]}
        if tokens.get("refresh_token"):  # Microsoft rotates it, Google sends it only on consent
            vals["refresh_token"] = tokens["refresh_token"]
        self._secret().write(vals)
        signed_in = oauth.email_from_id_token(tokens.get("id_token") or "")
        box_vals = {"connection_state": "connected", "last_error": False}
        if signed_in:
            box_vals["oauth_email"] = signed_in
            box_vals["login"] = signed_in
        self.sudo().write(box_vals)

    def _get_access_token(self):
        self.ensure_one()
        secret = self._secret()
        if secret.access_token and secret.token_expires_at > time.time():
            return secret.access_token
        if not secret.refresh_token:
            raise UserError(_("Mailbox %s is not signed in. Use 'Connect' again.", self.display_name))
        try:
            self._store_tokens(oauth.refresh(self.env, self.account_type, secret.refresh_token))
        except oauth.OAuthError as exc:
            self.sudo().write({"connection_state": "error", "last_error": str(exc)[:250]})
            raise UserError(_("%(box)s: the provider refused the session (%(err)s). Use 'Connect' again.",
                              box=self.display_name, err=exc))
        return secret.access_token

    # ------------------------------------------------------------------ IMAP / SMTP
    def _imap_connect(self):
        """Authenticated IMAP connection (password or XOAUTH2). Caller must logout()."""
        self.ensure_one()
        ctx = ssl.create_default_context()
        if self.imap_security == "ssl":
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ctx, timeout=30)
        else:
            conn = imaplib.IMAP4(self.imap_host, self.imap_port, timeout=30)
            if self.imap_security == "starttls":
                conn.starttls(ssl_context=ctx)
        try:
            if self.account_type == "imap":
                conn.login(self.login or self.email, self._secret().password or "")
            else:
                token = self._get_access_token()
                conn.authenticate("XOAUTH2", lambda _c: oauth.xoauth2(self.login or self.email, token).encode())
        except Exception:
            conn.shutdown()
            raise
        return conn

    def _smtp_connect(self):
        """Authenticated SMTP connection (password or XOAUTH2). Caller must quit()."""
        self.ensure_one()
        ctx = ssl.create_default_context()
        if self.smtp_security == "ssl":
            conn = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx, timeout=30)
        else:
            conn = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            if self.smtp_security == "starttls":
                conn.starttls(context=ctx)
        conn.ehlo()
        if self.account_type == "imap":
            conn.login(self.login or self.email, self._secret().password or "")
        else:
            import base64
            auth = base64.b64encode(oauth.xoauth2(self.login or self.email, self._get_access_token()).encode())
            code, resp = conn.docmd("AUTH", "XOAUTH2 " + auth.decode())
            if code != 235:
                conn.close()
                raise smtplib.SMTPAuthenticationError(code, resp)
        return conn

    def action_test_connection(self):
        """Log in to IMAP and SMTP, read the INBOX unread count, report in one notification."""
        self.ensure_one()
        self._check_role("manage")
        box = self.sudo()
        try:
            imap = box._imap_connect()
            try:
                imap.select("INBOX", readonly=True)
                typ, data = imap.search(None, "UNSEEN")
                unread = len(data[0].split()) if typ == "OK" and data and data[0] else 0
            finally:
                imap.logout()
            box._smtp_connect().quit()
        except Exception as exc:
            box.write({"connection_state": "error", "last_error": str(exc)[:250]})
            return self._notify(_("Connection failed"), str(exc), "danger")
        box.write({"connection_state": "connected", "last_error": False, "unread_count": unread,
                   "last_sync": fields.Datetime.now()})
        box._notify_counters()
        return self._notify(_("Connected"), _("IMAP and SMTP OK. %s unread in INBOX.", unread), "success")

    def action_sync_now(self):
        self.ensure_one()
        res = self.umc_sync_now()
        if res["errors"]:
            return self._notify(_("Synchronization failed"), "\n".join(res["errors"]), "danger")
        return self._notify(_("Synchronized"), _("%s new messages.", res["new"]), "success")

    @api.model
    def _notify(self, title, message, kind):
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": title, "message": message, "type": kind, "sticky": kind == "danger",
                           "next": {"type": "ir.actions.act_window_close"} if kind == "success" else False}}

    # ------------------------------------------------------------------ live counters (bus)
    def _notify_counters(self):
        """Push the unread counter of each mailbox to everyone with access (real time, no polling)."""
        for box in self.sudo():
            payload = {"mailbox_id": box.id, "unread": box.unread_count}
            for user in box.user_ids:
                user._bus_send("umc.counters", payload)

    @api.model
    def get_inbox_overview(self):
        """Entry point of the OWL unified inbox: my mailboxes, counters, role and plan."""
        boxes = self.search([])
        folders = self.env["umc.folder"].sudo().search([("mailbox_id", "in", boxes.ids), ("selectable", "=", True)])
        unified = {}
        for f in folders.filtered(lambda f: f.role != "other"):
            unified[f.role] = unified.get(f.role, 0) + (f.unread_count if f.role in ("inbox", "spam") else 0)
        return {
            "user_name": self.env.user.name,
            "mailboxes": [{
                "id": b.id, "name": b.name, "email": b.email, "type": b.mailbox_type, "account": b.account_type,
                "unread": b.unread_count, "role": b.my_role, "state": b.connection_state, "color": b.color,
                "error": b.last_error or "",
                "folders": [{"id": f.id, "name": f.name, "role": f.role, "unread": f.unread_count,
                             "sync": f.sync} for f in folders if f.mailbox_id == b],
            } for b in boxes],
            "unified": unified,
            "total_unread": sum(boxes.mapped("unread_count")),
        }


class UmcMailboxMember(models.Model):
    """One user's access to a team mailbox."""

    _name = "umc.mailbox.member"
    _description = "Email Client for Odoo Full - team mailbox member"
    _order = "mailbox_id, role desc, id"

    mailbox_id = fields.Many2one("umc.mailbox", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", "User", required=True, ondelete="cascade", index=True,
                              domain=[("share", "=", False)])
    role = fields.Selection(ROLES, required=True, default="reply")

    def init(self):
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS umc_mailbox_member_uniq "
                            "ON umc_mailbox_member (mailbox_id, user_id)")

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        members.mailbox_id._check_role("manage")
        for box in members.mailbox_id:
            if box.mailbox_type != "team":
                box.sudo().mailbox_type = "team"
        return members

    def write(self, vals):
        self.mailbox_id._check_role("manage")
        return super().write(vals)

    def unlink(self):
        self.mailbox_id._check_role("manage")
        return super().unlink()
