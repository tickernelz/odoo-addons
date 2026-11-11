import json
import logging
import queue
import uuid

from werkzeug.wrappers import Response

from odoo import http
from odoo.fields import Datetime
from odoo.http import request
from odoo.tools import config as _odoo_config

from ..services import mcp_protocol
from ..services.rate_limiter import get_rate_limiter
from ..services.sse_manager import session_manager
from ..services.token_service import extract_token, validate_token, unauthorized

_logger = logging.getLogger(__name__)

_workers = int(_odoo_config.get("workers", 0) or 0)
if _workers > 1:
    _logger.warning(
        "MCP Server: workers=%s detected. SSE sessions are stored in-memory per-worker. "
        "POST /mcp/messages may land on a different worker and return 404. "
        "Configure nginx with 'ip_hash' or sticky sessions to fix this.",
        _workers,
    )

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": (
        "Content-Type, Authorization, X-API-Key, X-Auth-Token, Mcp-Session-Id, Mcp-Protocol-Version"
    ),
}

# N2: HTTPS enforcement — set mcp_require_https = true in odoo.conf to enable.
# Automatically skipped in dev mode (workers=0 with dev_mode flag).
_REQUIRE_HTTPS = str(_odoo_config.get("mcp_require_https", "false")).lower() in ("1", "true", "yes")


def _https_guard() -> Response | None:
    """Return 400 if HTTPS is required but the request arrived over HTTP. None if OK."""
    if not _REQUIRE_HTTPS:
        return None
    if _odoo_config.get("dev_mode"):
        return None
    scheme = (
        request.httprequest.headers.get("X-Forwarded-Proto")
        or request.httprequest.environ.get("wsgi.url_scheme", "http")
    )
    if scheme != "https":
        return _json_resp(
            {"error": "https_required",
             "error_description": "This MCP endpoint requires HTTPS."},
            400,
        )
    return None


def _ip_guard(user_info: dict, ip: str) -> Response | None:
    """Return 403 if the client IP is blocked by the token's IP rules. None if OK.

    Loads the mcp.token record to call is_ip_allowed(). This read is cheap since
    the record is already in the ORM cache from validate_token's sudo search.
    """
    token_id = user_info.get("token_id")
    if not token_id:
        return None
    try:
        token_rec = request.env["mcp.token"].sudo().browse(token_id)
        if not token_rec.exists():
            return None
        if not token_rec.is_ip_allowed(ip):
            _logger.warning(
                "MCP: IP %r blocked by token %s (client=%s)", ip, token_id, user_info.get("client_name")
            )
            return _json_resp(
                {"error": "ip_blocked",
                 "error_description": "Your IP address is not permitted for this token."},
                403,
            )
    except Exception:
        _logger.exception("MCP: IP check failed for token %s", token_id)
    return None


class McpController(http.Controller):

    # ------------------------------------------------------------------
    # OPTIONS — CORS preflight for all MCP endpoints
    # ------------------------------------------------------------------

    @http.route(["/mcp", "/mcp/sse", "/mcp/messages", "/mcp/<string:database>",
                 "/mcp/<string:database>/sse", "/mcp/<string:database>/messages"],
                type="http", auth="public", methods=["OPTIONS"], csrf=False)
    def mcp_cors(self, database=None, **_kwargs):
        return Response("", status=200, headers=_CORS_HEADERS)

    # ------------------------------------------------------------------
    # POST /mcp  – Streamable HTTP transport (MCP spec 2025-03-26)
    # Recommended transport for Claude CLI, VSCode extension, and all
    # modern MCP clients. Stateless: no SSE session needed.
    # ------------------------------------------------------------------

    @http.route(["/mcp", "/mcp/<string:database>"], type="http", auth="public", methods=["POST"], csrf=False)
    def mcp_http(self, database=None, **_kwargs):
        return _other_database(database) or self._handle_streamable_http()

    # ------------------------------------------------------------------
    # POST /mcp/sse  – Streamable HTTP transport (legacy URL alias)
    # ------------------------------------------------------------------

    @http.route(["/mcp/sse", "/mcp/<string:database>/sse"], type="http", auth="public", methods=["POST"],
                csrf=False)
    def mcp_streamable(self, database=None, **_kwargs):
        return _other_database(database) or self._handle_streamable_http()

    def _handle_streamable_http(self):
        """Stateless JSON-RPC over HTTP POST. Finds or creates a persistent HTTP
        audit session per token so message_count and tool call logs are recorded."""
        guard = _https_guard()
        if guard:
            return guard

        token = extract_token(request)
        if not token:
            return unauthorized(_base_url())

        try:
            user_info = validate_token(token, request.env)
        except Exception:
            _logger.exception("MCP HTTP: token validation failed")
            return unauthorized(_base_url())

        if not user_info:
            return unauthorized(_base_url())

        ip = request.httprequest.remote_addr
        ip_block = _ip_guard(user_info, ip)
        if ip_block:
            return ip_block

        allowed, _remaining = get_rate_limiter(request.env).check(
            user_info["token_hash"], max_override=user_info.get("rate_limit", 0)
        )
        if not allowed:
            return _json_resp({"error": "rate_limit_exceeded", "retry_after": 60}, 429)

        try:
            body = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return _json_resp(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                400,
            )

        uid = user_info["uid"]
        token_id = user_info["token_id"]
        scope = user_info.get("scope", "write")
        restrictions = user_info.get("restrictions")
        capture_payloads = user_info.get("capture_payloads", False)

        # Find or create a persistent HTTP session record for this token
        session_db_id = _get_or_create_http_session(
            request.env, uid, token_id, ip
        )

        _logger.debug(
            "MCP HTTP: uid=%s scope=%s method=%s session_db=%s",
            uid,
            scope,
            body.get("method") if isinstance(body, dict) else "batch",
            session_db_id,
        )

        try:
            env = request.env(user=uid)
            if isinstance(body, list):
                responses = [
                    mcp_protocol.process_message(
                        env, msg,
                        session_db_id=session_db_id,
                        transport="http",
                        scope=scope,
                        restrictions=restrictions,
                        capture_payloads=capture_payloads,
                        token_id=token_id,
                    )
                    for msg in body
                ]
                responses = [r for r in responses if r is not None]
                _update_http_session(request.env, session_db_id, len(body))
                if not responses:
                    return Response("", status=202, headers=_CORS_HEADERS)
                return _json_resp(responses)
            else:
                response = mcp_protocol.process_message(
                    env, body,
                    session_db_id=session_db_id,
                    transport="http",
                    scope=scope,
                    restrictions=restrictions,
                    capture_payloads=capture_payloads,
                    token_id=token_id,
                )
                _update_http_session(request.env, session_db_id, 1)
                if response is None:
                    return Response("", status=202, headers=_CORS_HEADERS)
                return _json_resp(response)
        except Exception:
            _logger.exception("MCP HTTP: processing error uid=%s", uid)
            msg_id = body.get("id") if isinstance(body, dict) else None
            return _json_resp(
                {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": "Internal error"}},
                500,
            )

    # ------------------------------------------------------------------
    # GET /mcp/sse  – Legacy SSE transport (MCP spec pre-2025-03-26)
    # ------------------------------------------------------------------

    @http.route(["/mcp/sse", "/mcp/<string:database>/sse"], type="http", auth="public", methods=["GET"],
                csrf=False)
    def mcp_sse(self, database=None, **_kwargs):
        guard = _other_database(database) or _https_guard()
        if guard:
            return guard

        token = extract_token(request)
        if not token:
            return unauthorized(_base_url())

        try:
            user_info = validate_token(token, request.env)
        except Exception:
            _logger.exception("MCP SSE: token validation failed")
            return unauthorized(_base_url())

        if not user_info:
            return unauthorized(_base_url())

        ip = request.httprequest.remote_addr
        ip_block = _ip_guard(user_info, ip)
        if ip_block:
            return ip_block

        allowed, _remaining = get_rate_limiter(request.env).check(
            user_info["token_hash"], max_override=user_info.get("rate_limit", 0)
        )
        if not allowed:
            return _json_resp({"error": "rate_limit_exceeded", "retry_after": 60}, 429)

        uid = user_info["uid"]
        token_id = user_info["token_id"]
        scope = user_info.get("scope", "write")
        restrictions = user_info.get("restrictions")
        capture_payloads = user_info.get("capture_payloads", False)

        db = request.db
        mcp_session = session_manager.create(uid, db, ip)
        session_id = mcp_session.session_id

        # Store governance context on the in-memory session so the POST handler
        # (mcp_messages) can recover scope/restrictions without re-validating the token.
        mcp_session.scope = scope
        mcp_session.restrictions = restrictions
        mcp_session.capture_payloads = capture_payloads
        mcp_session.token_id = token_id

        _logger.info(
            "MCP SSE: new connection uid=%s scope=%s session=%s ip=%s",
            uid, scope, session_id, ip,
        )

        # Audit log — commit before streaming starts so cursor is clean
        try:
            rec = request.env["mcp.session"].sudo().create({
                "session_id": session_id,
                "user_id": uid,
                "token_id": token_id,
                "ip_address": ip,
                "transport": "sse",
                "state": "active",
            })
            request.env.cr.commit()
            mcp_session.db_id = rec.id
        except Exception:
            _logger.warning("Could not create mcp.session audit record")

        messages_url = f"{_base_url()}{_mcp_prefix()}/messages?session_id={session_id}"

        def _generate():
            try:
                yield f"event: endpoint\ndata: {messages_url}\n\n"
                while True:
                    try:
                        data = mcp_session.get(timeout=15)
                        if data is None:
                            break
                        yield f"event: message\ndata: {json.dumps(data, default=str)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            except Exception:
                _logger.exception("MCP SSE: generator error session=%s", session_id)
            finally:
                _logger.info("MCP SSE: session %s closed", session_id)
                session_manager.remove(session_id)

        return Response(
            _generate(),
            content_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                **_CORS_HEADERS,
            },
        )

    # ------------------------------------------------------------------
    # POST /mcp/messages  – Legacy SSE message channel
    # ------------------------------------------------------------------

    @http.route(["/mcp/messages", "/mcp/<string:database>/messages"], type="http", auth="public",
                methods=["POST"], csrf=False)
    def mcp_messages(self, session_id=None, database=None, **_kwargs):
        wrong = _other_database(database)
        if wrong:
            return wrong
        mcp_session = session_manager.get(session_id)
        if not mcp_session or mcp_session.db != request.db:
            _logger.warning("MCP messages: session not found: %s", session_id)
            return _json_resp({"error": "Session not found or expired"}, 404)

        allowed, _remaining = get_rate_limiter(request.env).check(f"session:{session_id}")
        if not allowed:
            return _json_resp({"error": "rate_limit_exceeded", "retry_after": 60}, 429)

        try:
            body = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return _json_resp({"error": "Invalid JSON"}, 400)

        _logger.debug("MCP message: session=%s method=%s", session_id, body.get("method"))

        # Recover governance context stored during SSE handshake
        scope = getattr(mcp_session, "scope", "write")
        restrictions = getattr(mcp_session, "restrictions", None)
        capture_payloads = getattr(mcp_session, "capture_payloads", False)

        try:
            env = request.env(user=mcp_session.uid)
            response = mcp_protocol.process_message(
                env, body,
                session_db_id=mcp_session.db_id,
                transport="sse",
                scope=scope,
                restrictions=restrictions,
                capture_payloads=capture_payloads,
                token_id=getattr(mcp_session, "token_id", None),
            )
            if response is not None:
                mcp_session.put(response)
        except Exception:
            _logger.exception("MCP message processing error session=%s", session_id)
            mcp_session.put({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32603, "message": "Internal error"},
            })

        # Update DB audit record (non-critical)
        try:
            env = request.env(user=mcp_session.uid)
            audit = env["mcp.session"].sudo().browse(mcp_session.db_id)
            if audit.exists():
                audit.write({
                    "message_count": mcp_session.message_count,
                    "last_activity": Datetime.now(),
                })
                env.cr.commit()
        except Exception:
            _logger.debug("mcp.session audit update failed for session=%s", session_id)

        return Response("", status=202, headers=_CORS_HEADERS)


# ---------------------------------------------------------------------------
# HTTP session helpers
# ---------------------------------------------------------------------------

def _get_or_create_http_session(env, uid: int, token_id: int, ip: str) -> int | None:
    """Return the mcp.session DB id for an HTTP transport token.

    Checks the in-memory cache first (O(1), no DB hit on repeat requests).
    Falls back to DB search + create only on first request per token per worker.
    """
    # Fast path: in-memory cache hit
    cached = session_manager.get_http_session_db_id(env.cr.dbname, token_id)
    if cached:
        return cached

    # Slow path: DB search or create (only happens once per token per worker)
    try:
        env_sudo = env["mcp.session"].sudo()
        existing = env_sudo.search(
            [("token_id", "=", token_id), ("transport", "=", "http"), ("state", "=", "active")],
            limit=1,
        )
        if existing:
            session_manager.set_http_session_db_id(env.cr.dbname, token_id, existing.id)
            return existing.id

        rec = env_sudo.create({
            "session_id": str(uuid.uuid4()),
            "user_id": uid,
            "token_id": token_id,
            "ip_address": ip,
            "transport": "http",
            "state": "active",
        })
        env.cr.commit()
        session_manager.set_http_session_db_id(env.cr.dbname, token_id, rec.id)
        return rec.id
    except Exception:
        _logger.debug("Could not find/create HTTP session record")
        return None


def _update_http_session(env, session_db_id: int | None, message_delta: int) -> None:
    """Increment message_count and update last_activity for an HTTP session."""
    if not session_db_id:
        return
    try:
        env.cr.execute(
            "UPDATE mcp_session SET message_count = message_count + %s, "
            "last_activity = NOW() AT TIME ZONE 'UTC' WHERE id = %s",
            (message_delta, session_db_id),
        )
        env.cr.commit()
    except Exception:
        _logger.debug("Could not update HTTP session stats db_id=%s", session_db_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _other_database(database: str | None) -> Response | None:
    """404 when the URL names a database other than the one serving the request."""
    if database is None or database == request.db:
        return None
    return _json_resp({"error": "not_found", "error_description": "No MCP endpoint here."}, 404)


def _mcp_prefix() -> str:
    """/mcp, or /mcp/<database> when the client reached the endpoint through its database path."""
    path = request.httprequest.path.rstrip("/")
    return path[:-len("/sse")] if path.endswith("/sse") else "/mcp"


def _base_url() -> str:
    req = request.httprequest
    proto = req.environ.get("HTTP_X_FORWARDED_PROTO") or req.scheme
    return f"{proto}://{req.host}"


def _json_resp(data, status: int = 200) -> Response:
    return Response(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        status=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )
