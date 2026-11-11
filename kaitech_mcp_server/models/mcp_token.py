import hashlib
import ipaddress
import json
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

TOKEN_TTL_DAYS = 30
_ADMIN_FIELDS = frozenset({
    "token", "user_id", "token_type", "expires_at", "last_used", "refresh_token", "refresh_token_expires_at",
    "allow_sql", "sql_table_ids", "allow_explore",
})
REFRESH_TOKEN_TTL_DAYS = 90


def _hash_token(raw: str) -> str:
    """SHA-256 hash of a raw token — only the hash is stored in DB."""
    return hashlib.sha256(raw.encode()).hexdigest()


class McpToken(models.Model):
    _name = "mcp.token"
    _description = "MCP Bearer Token"
    _inherit = ["mail.thread"]          # N3: chatter + audit trail
    _order = "create_date desc"
    _rec_name = "name"

    name = fields.Char(string="Client", default="MCP Client")
    token = fields.Char(string="Token Hash (SHA-256)", readonly=True, index=True, copy=False)
    user_id = fields.Many2one("res.users", string="User", required=True, ondelete="cascade", readonly=True)
    token_type = fields.Selection(
        [("oauth", "OAuth 2.0"), ("pat", "Personal Access Token")],
        string="Type", default="oauth", readonly=True,
    )
    state = fields.Selection(
        [("active", "Active"), ("revoked", "Revoked"), ("expired", "Expired")],
        default="active", readonly=True,
        tracking=True,                  # N3: log state changes in chatter
    )
    expires_at = fields.Datetime(string="Expires At", readonly=True)
    last_used = fields.Datetime(string="Last Used", readonly=True)

    # N1: refresh token fields (OAuth only — PAT has no refresh token)
    refresh_token = fields.Char(
        string="Refresh Token Hash", readonly=True, index=True, copy=False,
    )
    refresh_token_expires_at = fields.Datetime(string="Refresh Expires At", readonly=True)

    # ------------------------------------------------------------------
    # Feature 1: Per-token scope
    # ------------------------------------------------------------------
    scope = fields.Selection(
        [("read", "Read Only"), ("write", "Read + Write"), ("admin", "Full Access")],
        string="Scope",
        default="write",
        required=True,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Feature 2: IP allowlist / denylist
    # ------------------------------------------------------------------
    ip_allowlist = fields.Text(
        string="IP Allowlist",
        help="One CIDR range or IP address per line. Leave blank to allow all IPs. "
             "Supports IPv4, IPv6, and CIDR notation (e.g. 192.168.1.0/24). "
             "Lines starting with # are treated as comments.",
    )
    ip_denylist = fields.Text(
        string="IP Denylist",
        help="One CIDR range or IP address per line. Leave blank to deny none. "
             "Denylist is evaluated before allowlist. "
             "Lines starting with # are treated as comments.",
    )

    # ------------------------------------------------------------------
    # Feature 3: Model and field restrictions
    # ------------------------------------------------------------------
    allowed_model_ids = fields.Many2many(
        "ir.model",
        "mcp_token_allowed_model_rel",
        "token_id",
        "model_id",
        string="Allowed Models",
        help="Restrict this token to specific Odoo models. "
             "Leave empty to allow access to all models.",
    )
    denied_model_ids = fields.Many2many(
        "ir.model",
        "mcp_token_denied_model_rel",
        "token_id",
        "model_id",
        string="Denied Models",
        help="Block this token from specific Odoo models. "
             "Denylist is evaluated before allowlist.",
    )
    field_restrictions = fields.Text(
        string="Field Restrictions (JSON)",
        help='JSON object mapping model names to allowed field lists. Example:\n'
             '{"sale.order": ["name", "partner_id", "amount_total"],\n'
             ' "res.partner": ["name", "email"]}\n\n'
             'Omitting a model means all its fields are allowed. '
             'Applied to both read (silently drops disallowed fields) '
             'and write (raises error if disallowed field is written).',
    )

    # ------------------------------------------------------------------
    # Feature 4: Payload capture opt-in
    # ------------------------------------------------------------------
    capture_payloads = fields.Boolean(
        string="Capture Payloads",
        default=False,
        help="When enabled, tool call arguments and responses are stored in the audit log "
             "(after PII redaction). Disabled by default for performance.",
        tracking=True,
    )

    allow_sql = fields.Boolean(
        string="SQL",
        tracking=True,
        help="Lets this token use the SQL tools on the tables listed below. SQL bypasses Odoo access rights, "
             "record rules and constraints. SQL must also be switched on in MCP Server settings.",
    )
    sql_table_ids = fields.One2many("mcp.token.sql.table", "token_id", string="SQL Tables")
    allow_explore = fields.Boolean(
        string="Explore Code",
        tracking=True,
        help="Lets this token read the code of the installed modules: models, methods, source files, "
             "views and actions.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._privileged():
            for vals in vals_list:
                self._check_owner_fields(vals)
        if any(vals.get("scope") == "admin" for vals in vals_list):
            self._check_can_grant_full_access()
        return super().create(vals_list)

    def write(self, vals):
        if not self._privileged():
            self._check_owner_fields(vals)
        if vals.get("scope") == "admin":
            self._check_can_grant_full_access()
        return super().write(vals)

    def _privileged(self):
        return self.env.su or self.env.user._is_system()

    def _check_owner_fields(self, vals):
        """A token owner may rename, restrict or revoke a token, never re-own, re-key, revive or widen it."""
        protected = sorted(name for name in vals if name in _ADMIN_FIELDS)
        if protected:
            raise AccessError(_("Only Settings administrators can change %(fields)s on an MCP token.",
                                fields=", ".join(protected)))
        if "state" in vals and vals["state"] != "revoked":
            raise AccessError(_("An MCP token can only be revoked here."))

    def _check_can_grant_full_access(self):
        if not self._privileged():
            raise AccessError(_("Only Settings administrators can grant Full Access to an MCP token."))

    # ------------------------------------------------------------------
    # Token issuance
    # ------------------------------------------------------------------

    @api.private
    @api.model
    def issue(self, uid: int, client_name: str = "MCP Client",
              token_type: str = "oauth", scope: str | None = None) -> str:
        """Issue a new access token. Returns raw access token (shown once, not stored).
        For OAuth flows with refresh token, use issue_oauth() instead."""
        raw, _refresh = self._issue_pair(uid, client_name, token_type, scope=scope)
        return raw

    @api.private
    @api.model
    def issue_oauth(self, uid: int, client_name: str = "MCP Client") -> tuple[str, str]:
        """Issue access + refresh token pair for OAuth flows.
        Returns (raw_access_token, raw_refresh_token)."""
        return self._issue_pair(uid, client_name, "oauth")

    @api.model
    def _issue_pair(self, uid: int, client_name: str,
                    token_type: str, scope: str | None = None) -> tuple[str, str | None]:
        """Internal: create mcp.token record, return (raw_access, raw_refresh|None)."""
        raw_access = secrets.token_urlsafe(32)
        now = fields.Datetime.now()
        vals = {
            "name": client_name,
            "token": _hash_token(raw_access),
            "user_id": uid,
            "token_type": token_type,
            "expires_at": now + timedelta(days=TOKEN_TTL_DAYS),
        }
        if scope:
            vals["scope"] = scope
        raw_refresh = None
        if token_type == "oauth":
            raw_refresh = secrets.token_urlsafe(32)
            vals["refresh_token"] = _hash_token(raw_refresh)
            vals["refresh_token_expires_at"] = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)

        rec = self.with_user(uid).sudo().create(vals)
        rec.message_post(body=f"MCP Bearer Token issued — client: {client_name}")
        return raw_access, raw_refresh

    @api.private
    @api.model
    def refresh(self, raw_refresh_token: str) -> tuple[str, str] | None:
        """Exchange a refresh token for a new access + refresh token pair (rotation).
        Old token is expired. Returns (new_access, new_refresh) or None if invalid."""
        token_hash = _hash_token(raw_refresh_token)
        rec = self.search(
            [("refresh_token", "=", token_hash), ("state", "=", "active")],
            limit=1,
        )
        if not rec:
            return None

        now = fields.Datetime.now()
        if rec.refresh_token_expires_at and now > rec.refresh_token_expires_at:
            rec.write({"state": "expired"})
            return None

        # Issue replacement tokens
        new_raw_access = secrets.token_urlsafe(32)
        new_raw_refresh = secrets.token_urlsafe(32)
        self.create({
            "name": rec.name,
            "token": _hash_token(new_raw_access),
            "refresh_token": _hash_token(new_raw_refresh),
            "user_id": rec.user_id.id,
            "token_type": rec.token_type,
            "expires_at": now + timedelta(days=TOKEN_TTL_DAYS),
            "refresh_token_expires_at": now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
            **rec._governance_values(),
        })
        # Expire the used token (rotation — prevent reuse)
        rec.write({"state": "expired"})
        return new_raw_access, new_raw_refresh

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _governance_values(self):
        """Scope and restrictions a rotated token keeps."""
        self.ensure_one()
        return {
            "scope": self.scope,
            "ip_allowlist": self.ip_allowlist,
            "ip_denylist": self.ip_denylist,
            "allowed_model_ids": [(6, 0, self.allowed_model_ids.ids)],
            "denied_model_ids": [(6, 0, self.denied_model_ids.ids)],
            "field_restrictions": self.field_restrictions,
            "capture_payloads": self.capture_payloads,
            "allow_sql": self.allow_sql,
            "allow_explore": self.allow_explore,
            "sql_table_ids": [(0, 0, row._copy_values()) for row in self.sql_table_ids],
        }

    def action_revoke(self):
        self.write({"state": "revoked"})

    def action_generate_pat(self):
        """Issue a PAT for the current user and open one-time display wizard.

        A Settings administrator's PAT gets Full Access; other users get Read + Write."""
        raw = self.issue(
            self.env.uid,
            client_name=f"PAT — {self.env.user.name}",
            token_type="pat",
            scope="admin" if self.env.user._is_system() else "write",
        )
        token_rec = self.search(
            [("user_id", "=", self.env.uid), ("token_type", "=", "pat")],
            order="create_date desc",
            limit=1,
        )
        wizard = self.env["mcp.pat.wizard"].create({
            "token_id": token_rec.id,
            "raw_token": raw,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "mcp.pat.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": "Personal Access Token — Save This Now",
        }

    # ------------------------------------------------------------------
    # Governance helpers (called from token_service / tool_executor)
    # ------------------------------------------------------------------

    def is_ip_allowed(self, ip: str) -> bool:
        """Return True if *ip* is permitted by this token's IP rules.

        Evaluation order: denylist checked first, then allowlist.
        - If IP is in denylist → False
        - If allowlist is non-empty and IP is NOT in allowlist → False
        - Otherwise → True
        """
        self.ensure_one()
        try:
            client = ipaddress.ip_address(ip)
        except ValueError:
            # Unparseable IP — fail safe (deny)
            _logger.warning("MCP token %s: could not parse client IP %r", self.id, ip)
            return False

        if self.ip_denylist:
            for entry in self._parse_ip_lines(self.ip_denylist):
                if client in entry:
                    return False

        if self.ip_allowlist:
            for entry in self._parse_ip_lines(self.ip_allowlist):
                if client in entry:
                    return True
            # Allowlist is non-empty but no match
            return False

        return True

    def is_model_allowed(self, model_name: str) -> bool:
        """Return True if *model_name* is accessible under this token's model restrictions.

        Denied models are checked first. If allowed_model_ids is non-empty, the
        model must appear in it. Empty allowed_model_ids means all models are allowed.
        """
        self.ensure_one()
        if self.denied_model_ids:
            denied_names = self.denied_model_ids.mapped("model")
            if model_name in denied_names:
                return False
        if self.allowed_model_ids:
            allowed_names = self.allowed_model_ids.mapped("model")
            if model_name not in allowed_names:
                return False
        return True

    def get_allowed_fields(self, model_name: str) -> list | None:
        """Return the list of allowed field names for *model_name*, or None if all are allowed.

        Returns None (not an empty list) to distinguish "no restriction" from
        "restriction exists but produces empty set" (the latter would be a misconfiguration).
        """
        self.ensure_one()
        if not self.field_restrictions:
            return None
        try:
            restrictions = json.loads(self.field_restrictions)
        except (json.JSONDecodeError, TypeError):
            _logger.warning(
                "MCP token %s: invalid JSON in field_restrictions — treating as no restriction",
                self.id,
            )
            return None
        if not isinstance(restrictions, dict):
            return None
        model_fields = restrictions.get(model_name)
        if model_fields is None:
            return None
        if not isinstance(model_fields, list):
            return None
        return [str(f) for f in model_fields]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ip_lines(text: str):
        """Parse multi-line IP/CIDR text, yield ipaddress network/address objects.

        Blank lines and lines starting with # are skipped. Malformed entries are
        logged and skipped so one bad line does not break the entire check.
        """
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield ipaddress.ip_network(line, strict=False)
            except ValueError:
                try:
                    # Single address not in CIDR notation
                    yield ipaddress.ip_network(
                        str(ipaddress.ip_address(line)), strict=False
                    )
                except ValueError:
                    _logger.warning("MCP token: could not parse IP entry %r — skipping", line)
