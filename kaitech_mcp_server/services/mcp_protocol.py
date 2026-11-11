import json
import logging
import time

from . import tool_executor
from . import resource_service
from .tool_executor import odoo_json_default
from .redaction import redact

_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "Odoo MCP Server", "version": "11.0.0"}

_PAYLOAD_MAX_CHARS = 4000


def process_message(env, msg: dict, session_db_id: int | None = None,
                    transport: str = "http",
                    scope: str = "write",
                    restrictions: dict | None = None,
                    capture_payloads: bool = False) -> dict | None:
    """
    Process one JSON-RPC 2.0 MCP message and return the response dict,
    or None for notifications that require no reply.

    session_db_id:    DB id of mcp.session record (for tool call logging).
    transport:        'sse' or 'http' — recorded in mcp.session.log.
    scope:            Token scope — 'read' | 'write' | 'admin'.
                      Injected into env.context['mcp_scope'] so tool_executor
                      can enforce it without an extra DB lookup.
    restrictions:     Dict with 'allowed_models', 'denied_models',
                      'field_restrictions' — loaded once during token validation.
                      Injected into env.context['mcp_restrictions'].
    capture_payloads: When True, tool args and results are stored in audit log
                      (after PII redaction + truncation).
    """
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Inject governance context so all downstream calls (tool_executor, etc.)
    # can read it without carrying extra parameters through every call stack.
    ctx_updates = {"mcp_scope": scope}
    if restrictions:
        ctx_updates["mcp_restrictions"] = restrictions
    env = env(context={**env.context, **ctx_updates})
    # Full Access milik admin Settings berjalan sebagai superuser: ACL, record
    # rule, dan field groups= tidak lagi membatasi. Pemilik token non-admin tetap
    # tunduk pada hak akses Odoo-nya sendiri.
    if scope == "admin" and env.user._is_system():
        env = env(su=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    if method == "initialize":
        return _ok(
            msg_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": SERVER_INFO,
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(msg_id, {})

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    if method == "tools/list":
        return _ok(msg_id, {"tools": env["mcp.tool.registry"].get_tools()})

    if method == "tools/call":
        return _handle_tool_call(
            env, msg_id, params, session_db_id, transport, capture_payloads
        )

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    if method == "resources/list":
        return _ok(msg_id, {"resources": resource_service.RESOURCES})

    if method == "resources/templates/list":
        return _ok(msg_id, {"resourceTemplates": resource_service.RESOURCE_TEMPLATES})

    if method == "resources/read":
        return _handle_resource_read(env, msg_id, params)

    # ------------------------------------------------------------------
    # Prompts (not implemented — return empty list for client compatibility)
    # ------------------------------------------------------------------
    if method == "prompts/list":
        return _ok(msg_id, {"prompts": []})

    if method == "prompts/get":
        return _err(msg_id, -32602, "No prompts are defined on this server.")

    # ------------------------------------------------------------------
    # Unknown
    # ------------------------------------------------------------------
    if msg_id is not None:
        return _err(msg_id, -32601, f"Method not found: {method}")

    return None


# ---------------------------------------------------------------------------
# Tool call handler
# ---------------------------------------------------------------------------

def _handle_tool_call(env, msg_id, params: dict,
                      session_db_id: int | None, transport: str,
                      capture_payloads: bool) -> dict:
    tool_name = params.get("name")
    tool_input = params.get("arguments") or {}
    t0 = time.monotonic()
    try:
        # Savepoint: tool yang gagal (mis. ValidationError dari constraint) harus
        # tidak meninggalkan jejak. Tanpa ini INSERT/UPDATE yang sudah terjadi
        # sebelum exception ikut ter-commit bersama log sesi, sehingga record yang
        # "ditolak" tetap tersimpan (temuan #5 E2E Pengadaan 5 Oktober 2026).
        with env.cr.savepoint():
            result = tool_executor.execute_tool(env, tool_name, tool_input)
        duration_ms = int((time.monotonic() - t0) * 1000)
        req_payload, res_payload = _build_payloads(capture_payloads, tool_input, result)
        _log_tool_call(env, session_db_id, transport, tool_name, tool_input,
                       result, duration_ms, is_error=False,
                       request_payload=req_payload, response_payload=res_payload)
        text = json.dumps(result, default=odoo_json_default, ensure_ascii=False, indent=2)
        return _ok(
            msg_id,
            {"content": [{"type": "text", "text": text}], "isError": False},
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        req_payload, _ = _build_payloads(capture_payloads, tool_input, None)
        _log_tool_call(env, session_db_id, transport, tool_name, tool_input,
                       {}, duration_ms, is_error=True, error_message=str(exc),
                       request_payload=req_payload, response_payload=None)
        _logger.exception("MCP tool %r error", tool_name)
        return _ok(
            msg_id,
            {"content": [{"type": "text", "text": str(exc)}], "isError": True},
        )


def _build_payloads(capture: bool, args: dict, result) -> tuple[str | None, str | None]:
    """Return (request_payload_str, response_payload_str) when capture=True, else (None, None)."""
    if not capture:
        return None, None
    try:
        redacted_args = redact(args)
        req = json.dumps(redacted_args, default=odoo_json_default, ensure_ascii=False)
        req = req[:_PAYLOAD_MAX_CHARS]
    except Exception:
        req = None

    res = None
    if result is not None:
        try:
            redacted_result = redact(result)
            res = json.dumps(redacted_result, default=odoo_json_default, ensure_ascii=False)
            res = res[:_PAYLOAD_MAX_CHARS]
        except Exception:
            res = None

    return req, res


def _log_tool_call(env, session_db_id, transport, tool_name, tool_input,
                   result, duration_ms, is_error=False, error_message="",
                   request_payload=None, response_payload=None):
    """Create mcp.session.log record. Non-critical — silently ignored on failure."""
    try:
        odoo_model = tool_input.get("model") or ""
        record_count = 0
        if not is_error and isinstance(result, dict):
            record_count = (
                result.get("total")
                or result.get("count")
                or result.get("updated")
                or result.get("deleted")
                or (1 if "id" in result else 0)
            )

        vals = {
            "session_id": session_db_id or False,
            "user_id": env.uid,
            "tool_name": tool_name or "",
            "odoo_model": odoo_model or False,
            "record_count": record_count or 0,
            "duration_ms": duration_ms,
            "is_error": is_error,
            "error_message": (error_message[:255] if error_message else False),
            "transport": transport,
        }
        if request_payload is not None:
            vals["request_payload"] = request_payload
        if response_payload is not None:
            vals["response_payload"] = response_payload

        env["mcp.session.log"].sudo().create(vals)
    except Exception:
        _logger.debug("Failed to create mcp.session.log", exc_info=True)


# ---------------------------------------------------------------------------
# Resource read handler
# ---------------------------------------------------------------------------

def _handle_resource_read(env, msg_id, params: dict) -> dict:
    uri = params.get("uri", "")
    try:
        content = resource_service.read_resource(uri, env)
        return _ok(
            msg_id,
            {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": content}
                ]
            },
        )
    except Exception as exc:
        _logger.exception("MCP resource read error: %s", uri)
        return _err(msg_id, -32002, str(exc))


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _ok(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
