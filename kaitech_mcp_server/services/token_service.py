import hashlib
import json
import logging
from datetime import timedelta

from werkzeug.wrappers import Response

from odoo.fields import Datetime

_logger = logging.getLogger(__name__)

# Only write last_used to DB if this much time has elapsed since the last update.
# Prevents a write transaction per request under high load.
_LAST_USED_THROTTLE_MINUTES = 5


def _hash_token(raw: str) -> str:
    """SHA-256 hash of a raw Bearer token — mirrors mcp_token.py hashing."""
    return hashlib.sha256(raw.encode()).hexdigest()


KEY_HEADERS = ("Authorization", "X-API-Key", "X-Auth-Token")


def extract_token(request) -> str | None:
    """The token of the first key header that carries one: a Bearer Authorization, or X-API-Key / X-Auth-Token
    with or without the Bearer prefix."""
    headers = request.httprequest.headers
    for name in KEY_HEADERS:
        value = (headers.get(name) or "").strip()
        scheme, _space, rest = value.partition(" ")
        if scheme.lower() == "bearer":
            value = rest.strip()
        elif name == "Authorization":
            value = ""
        if value:
            return value
    return None


def validate_token(token: str, env) -> dict | None:
    """
    Validate a Bearer token. Hashes the raw token before DB lookup so the
    database never stores or compares raw token values.

    Returns a dict with:
        uid             – Odoo user id
        client_name     – token display name
        token_id        – mcp.token record id
        token_hash      – used by rate limiter, never logged
        rate_limit      – per-user override (0 = use global default)
        scope           – 'read' | 'write' | 'admin'
        capture_payloads – bool, whether to capture tool args/results in audit log
        restrictions    – dict ready for env.context['mcp_restrictions']:
            {
                'allowed_models': set of model names (empty = all allowed),
                'denied_models':  set of model names (empty = none denied),
                'field_restrictions': dict {model_name: [field, ...]} (empty = all allowed),
            }

    Returns None if the token is invalid, expired, or revoked.

    last_used is throttled: written at most once every 5 minutes to avoid
    a write transaction on every single HTTP request under high load.
    """
    if not token:
        return None

    token_hash = _hash_token(token)
    rec = env["mcp.token"].sudo().search(
        [("token", "=", token_hash), ("state", "=", "active")],
        limit=1,
    )
    if not rec:
        return None

    now = Datetime.now()

    if rec.expires_at and now > rec.expires_at:
        rec.write({"state": "expired"})
        return None

    # Throttle: only update last_used if never set OR stale by > threshold
    threshold = now - timedelta(minutes=_LAST_USED_THROTTLE_MINUTES)
    if not rec.last_used or rec.last_used < threshold:
        rec.write({"last_used": now})

    # Full Access means no model or field restriction, even when the token still
    # holds an allowlist or denylist from an earlier configuration.
    full_access = rec.scope == "admin"

    # Build model restrictions set — load once here so tool_executor never re-queries
    allowed_models = set(rec.allowed_model_ids.mapped("model")) if rec.allowed_model_ids else set()
    denied_models = set(rec.denied_model_ids.mapped("model")) if rec.denied_model_ids else set()

    if full_access:
        allowed_models = denied_models = set()

    field_restrictions = {}
    if rec.field_restrictions and not full_access:
        try:
            parsed = json.loads(rec.field_restrictions)
            if isinstance(parsed, dict):
                field_restrictions = {k: v for k, v in parsed.items() if isinstance(v, list)}
        except (json.JSONDecodeError, TypeError):
            _logger.warning(
                "MCP token %s: invalid JSON in field_restrictions — ignoring", rec.id
            )

    return {
        "uid": rec.user_id.id,
        "client_name": rec.name,
        "token_id": rec.id,
        "token_hash": token_hash,        # passed to rate limiter — never logged
        "rate_limit": rec.user_id.mcp_rate_limit or 0,  # N5: 0 = use global default
        "scope": rec.scope or "write",
        "capture_payloads": bool(rec.capture_payloads),
        "restrictions": {
            "allowed_models": allowed_models,
            "denied_models": denied_models,
            "field_restrictions": field_restrictions,
        },
    }


def unauthorized(base_url: str) -> Response:
    """
    Return 401 with WWW-Authenticate pointing to OAuth discovery.
    Claude Code CLI auto-triggers OAuth flow when it sees this header.
    """
    return Response(
        json.dumps({"error": "unauthorized", "error_description": "Valid Bearer token required."}),
        status=401,
        content_type="application/json",
        headers={
            "WWW-Authenticate": (
                f'Bearer realm="{base_url}", '
                f'resource_metadata="{base_url}/.well-known/oauth-protected-resource/mcp/sse"'
            )
        },
    )
