import base64
import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models

CODE_TTL_MINUTES = 10


def _hash_code(raw: str) -> str:
    """SHA-256 hash of a raw auth code — mirrors mcp_token.py hashing."""
    return hashlib.sha256(raw.encode()).hexdigest()


class McpAuthCode(models.Model):
    """Short-lived OAuth 2.0 authorization codes (PKCE S256)."""

    _name = "mcp.auth.code"
    _description = "MCP OAuth Authorization Code"
    _order = "create_date desc"

    code = fields.Char(string="Code Hash (SHA-256)", readonly=True, index=True, copy=False)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    client_id = fields.Char()
    client_name = fields.Char()
    redirect_uri = fields.Char()
    code_challenge = fields.Char(help="PKCE S256 code challenge")
    expires_at = fields.Datetime(readonly=True)
    used = fields.Boolean(default=False, readonly=True)

    @api.private
    @api.model
    def create_code(
        self,
        uid: int,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        client_name: str = "",
    ) -> str:
        """Issue a one-time authorization code. Stores SHA-256 hash — returns raw code."""
        raw = secrets.token_urlsafe(32)
        self.create(
            {
                "code": _hash_code(raw),
                "user_id": uid,
                "client_id": client_id,
                "client_name": client_name,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "expires_at": fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
            }
        )
        return raw

    @api.private
    @api.model
    def exchange(self, code: str, code_verifier: str, redirect_uri: str):
        """
        Exchange code + PKCE verifier for the auth code record.
        Returns the record (with user_id) on success, None on failure.
        Marks the code as used to prevent replay.
        """
        rec = self.search(
            [
                ("code", "=", _hash_code(code)),
                ("used", "=", False),
                ("redirect_uri", "=", redirect_uri),
            ],
            limit=1,
        )
        if not rec:
            return None

        if fields.Datetime.now() > rec.expires_at:
            return None

        # Verify PKCE S256: SHA-256(code_verifier) == base64url(code_challenge)
        digest = hashlib.sha256(code_verifier.encode()).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if computed != rec.code_challenge:
            return None

        rec.write({"used": True})
        return rec

    @api.model
    def _vacuum_expired(self):
        """Delete used or expired auth codes. Called by ir.cron every hour."""
        deadline = fields.Datetime.now() - timedelta(minutes=CODE_TTL_MINUTES)
        old = self.search(["|", ("used", "=", True), ("expires_at", "<", deadline)])
        old.unlink()
