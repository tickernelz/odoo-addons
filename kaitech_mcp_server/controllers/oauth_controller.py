"""
OAuth 2.0 Authorization Server — MCP spec compliant.

Endpoints:
  GET  /.well-known/oauth-authorization-server  discovery metadata
  GET  /.well-known/oauth-protected-resource    resource metadata
  GET  /oauth/register                          registration capability metadata
  POST /oauth/register                          dynamic client registration (RFC 7591)
  GET  /oauth/authorize                         show Odoo login form
  POST /oauth/authorize                         authenticate + issue authorization code
  POST /oauth/token                             exchange code for Bearer token (PKCE S256)
  POST /oauth/revoke                            revoke a Bearer token

Auth is delegated entirely to Odoo res.users — no separate user database.
"""

import json
import logging
import urllib.parse
from contextlib import ExitStack

from werkzeug.wrappers import Response

import odoo
import odoo.modules.registry
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# CORS is handled entirely by nginx. Do NOT add headers here to avoid duplicates.

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #f0f4ff; display: flex; min-height: 100vh;
  align-items: center; justify-content: center;
}
.card {
  background: #fff; border: 1px solid #dde3f0; border-radius: 16px;
  padding: 40px; width: 100%; max-width: 400px;
  box-shadow: 0 4px 24px rgba(0,0,0,.08);
}
.brand {
  font-size: 12px; font-weight: 700; color: #875a7b;
  letter-spacing: .1em; text-transform: uppercase; margin-bottom: 20px;
}
h1 { font-size: 22px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
.sub { font-size: 14px; color: #64748b; margin-bottom: 24px; line-height: 1.5; }
.sub strong { color: #0f172a; }
.scope-box {
  background: #f8f5ff; border: 1px solid #e0d5f0;
  border-radius: 8px; padding: 12px 14px;
  font-size: 13px; color: #5a3e7a; margin-bottom: 20px;
}
label {
  display: block; font-size: 13px; font-weight: 500;
  color: #374151; margin-bottom: 5px;
}
input[type=text], input[type=email], input[type=password] {
  width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
  border-radius: 8px; font-size: 14px; outline: none;
  transition: border-color .15s; margin-bottom: 16px;
}
input:focus { border-color: #875a7b; box-shadow: 0 0 0 3px rgba(135,90,123,.12); }
.btn {
  width: 100%; padding: 11px; border: none; border-radius: 8px;
  font-size: 14px; font-weight: 600; cursor: pointer;
  background: #875a7b; color: #fff; transition: background .15s;
}
.btn:hover { background: #6d4963; }
.error {
  background: #fef2f2; border: 1px solid #fecaca; color: #dc2626;
  border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 16px;
}
"""


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _redirect(uri: str, params: dict) -> Response:
    sep = "&" if "?" in uri else "?"
    location = uri + sep + urllib.parse.urlencode(params)
    return Response(status=302, headers={"Location": location})


def _login_page(
    client_name: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    db: str,
    error: str = "",
) -> Response:
    error_html = f'<div class="error">{_esc(error)}</div>' if error else ""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Authorize — Odoo MCP Server</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="card">
    <div class="brand">Odoo MCP Server</div>
    <h1>Authorize Access</h1>
    <p class="sub">
      <strong>{_esc(client_name or "MCP Client")}</strong>
      is requesting access to your Odoo account.
    </p>
    {error_html}
    <div class="scope-box">
      <strong>Permissions requested:</strong><br>
      Full Odoo access (read + write) subject to your user permissions.
    </div>
    <form method="POST" autocomplete="on">
      <input type="hidden" name="client_id"      value="{_esc(client_id)}">
      <input type="hidden" name="redirect_uri"   value="{_esc(redirect_uri)}">
      <input type="hidden" name="code_challenge" value="{_esc(code_challenge)}">
      <input type="hidden" name="state"          value="{_esc(state)}">
      <input type="hidden" name="db"             value="{_esc(db)}">
      <label for="login">Odoo Login</label>
      <input id="login" name="login" type="text"
             autocomplete="username" placeholder="admin" autofocus>
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" placeholder="••••••••">
      <button type="submit" class="btn">Authorize</button>
    </form>
  </div>
</body>
</html>"""
    return Response(html, content_type="text/html; charset=utf-8")


def _json_resp(data: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json",
    )


def _base_url() -> str:
    req = request.httprequest
    proto = req.environ.get("HTTP_X_FORWARDED_PROTO") or req.scheme
    return f"{proto}://{req.host}"


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class OAuthController(http.Controller):

    # ------------------------------------------------------------------
    # Discovery endpoints (MCP spec requirement for auto-discovery)
    # ------------------------------------------------------------------

    @http.route(
        "/.well-known/oauth-authorization-server",
        type="http", auth="public", methods=["GET"], csrf=False,
    )
    def oauth_server_metadata(self, **_kw):
        base = _base_url()
        return _json_resp({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    @http.route(
        "/.well-known/openid-configuration",
        type="http", auth="public", methods=["GET"], csrf=False,
    )
    def openid_configuration(self, **_kw):
        """OpenID Connect discovery — required by ChatGPT and other OIDC clients.

        Maps our OAuth 2.0 server metadata to the OpenID Connect Discovery 1.0
        format so clients that probe /.well-known/openid-configuration can
        auto-configure without error.
        """
        base = _base_url()
        return _json_resp({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["read", "write", "admin"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "claims_supported": ["sub", "name", "email"],
        })

    @http.route(
        [
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource/mcp/sse",
        ],
        type="http", auth="public", methods=["GET"], csrf=False,
    )
    def oauth_resource_metadata(self, **_kw):
        base = _base_url()
        return _json_resp({
            "resource": f"{base}/mcp/sse",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
        })

    # ------------------------------------------------------------------
    # Dynamic client registration (RFC 7591) — stateless, public clients
    # GET: capability metadata (Claude.ai checks this before POST)
    # POST: register and receive client_id
    # ------------------------------------------------------------------

    @http.route(
        "/oauth/register",
        type="http", auth="public", methods=["GET", "POST", "OPTIONS"], csrf=False,
    )
    def oauth_register(self, **_kw):
        method = request.httprequest.method

        if method == "OPTIONS":
            return Response("", status=200)

        if method == "GET":
            base = _base_url()
            return _json_resp({
                "registration_endpoint": f"{base}/oauth/register",
                "grant_types_supported": ["authorization_code"],
                "response_types_supported": ["code"],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
            })

        # POST — issue a new client_id (stateless, public client)
        try:
            body = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return _json_resp({"error": "invalid_request"}, 400)

        import secrets
        return _json_resp(
            {
                "client_id": secrets.token_urlsafe(16),
                "client_name": body.get("client_name", "MCP Client"),
                "redirect_uris": body.get("redirect_uris", []),
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            201,
        )

    # ------------------------------------------------------------------
    # Authorization endpoint — shows Odoo login form
    # ------------------------------------------------------------------

    @http.route(
        "/oauth/authorize",
        type="http", auth="public", methods=["GET"], csrf=False,
    )
    def oauth_authorize_get(self, **params):
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "")
        state = params.get("state", "")
        client_name = params.get("client_name", "MCP Client")

        if not redirect_uri or not code_challenge:
            return _json_resp({"error": "invalid_request", "error_description": "redirect_uri and code_challenge are required"}, 400)

        if code_challenge_method and code_challenge_method != "S256":
            return _redirect(redirect_uri, {"error": "invalid_request", "error_description": "Only S256 is supported", "state": state})

        return _login_page(
            client_name=client_name,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            db=request.db or "",
        )

    @http.route(
        "/oauth/authorize",
        type="http", auth="public", methods=["POST"], csrf=False,
    )
    def oauth_authorize_post(self, **_kw):
        form = request.httprequest.form
        client_id = form.get("client_id", "")
        redirect_uri = form.get("redirect_uri", "")
        code_challenge = form.get("code_challenge", "")
        state = form.get("state", "")
        client_name = form.get("client_name", "MCP Client")
        db = form.get("db") or request.db
        login = form.get("login", "").strip()
        password = form.get("password", "")

        def _error(msg: str) -> Response:
            return _login_page(
                client_name=client_name,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                state=state,
                db=db,
                error=msg,
            )

        if not login:
            return _error("Login is required.")
        if not password:
            return _error("Password is required.")

        # Delegate authentication to Odoo res.users.
        # Odoo 19 signature: authenticate(env, credential_dict) — env replaces
        # the plain db name so the session can bind to an existing request
        # environment when available, or open a fresh registry cursor otherwise.
        try:
            db = db or request.db
            if not http.db_filter([db]):
                return _error("Invalid database.")
            with ExitStack() as stack:
                if not request.db or request.db != db:
                    cr = stack.enter_context(odoo.modules.registry.Registry(db).cursor())
                    env = odoo.api.Environment(cr, None, {})
                else:
                    env = request.env
                credential = {'login': login, 'password': password, 'type': 'password'}
                auth_info = request.session.authenticate(env, credential)
                uid = auth_info.get('uid') if isinstance(auth_info, dict) else auth_info
                request.session.db = db
                request._save_session(env)
        except Exception as exc:
            _logger.warning("OAuth authorize auth error for %r: %s", login, exc)
            return _error("Authentication error. Please try again.")

        if not uid:
            return _error("Invalid login or password.")

        # Issue authorization code (stored in mcp.auth.code)
        code = request.env["mcp.auth.code"].sudo().create_code(
            uid=uid,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            client_name=client_name,
        )

        return _redirect(redirect_uri, {"code": code, "state": state})

    # ------------------------------------------------------------------
    # Token endpoint — exchange authorization code for Bearer token
    # ------------------------------------------------------------------

    @http.route(
        "/oauth/token",
        type="http", auth="public", methods=["POST"], csrf=False,
    )
    def oauth_token(self, **_kw):
        form = request.httprequest.form
        grant_type = form.get("grant_type", "")

        if grant_type == "refresh_token":
            return self._handle_refresh_token(form)

        if grant_type != "authorization_code":
            return _json_resp({"error": "unsupported_grant_type"}, 400)

        code = form.get("code", "")
        redirect_uri = form.get("redirect_uri", "")
        code_verifier = form.get("code_verifier", "")
        client_name = form.get("client_name", "MCP Client")

        if not code or not redirect_uri or not code_verifier:
            return _json_resp(
                {"error": "invalid_request",
                 "error_description": "code, redirect_uri, and code_verifier are required"},
                400,
            )

        auth_code = request.env["mcp.auth.code"].sudo().exchange(code, code_verifier, redirect_uri)
        if not auth_code:
            return _json_resp(
                {"error": "invalid_grant",
                 "error_description": "Invalid, expired, or already used authorization code"},
                400,
            )

        client_label = auth_code.client_name or client_name
        raw_access, raw_refresh = request.env["mcp.token"].sudo().issue_oauth(
            uid=auth_code.user_id.id,
            client_name=client_label,
        )

        return _json_resp({
            "access_token": raw_access,
            "token_type": "Bearer",
            "expires_in": 30 * 86400,
            "refresh_token": raw_refresh,
            "refresh_token_expires_in": 90 * 86400,
        })

    def _handle_refresh_token(self, form):
        """Exchange a refresh_token for new access + refresh tokens (rotation)."""
        raw_refresh = form.get("refresh_token", "")
        if not raw_refresh:
            return _json_resp(
                {"error": "invalid_request", "error_description": "refresh_token is required"},
                400,
            )

        result = request.env["mcp.token"].sudo().refresh(raw_refresh)
        if not result:
            return _json_resp(
                {"error": "invalid_grant",
                 "error_description": "Refresh token is invalid, expired, or already used"},
                400,
            )

        new_access, new_refresh = result
        return _json_resp({
            "access_token": new_access,
            "token_type": "Bearer",
            "expires_in": 30 * 86400,
            "refresh_token": new_refresh,
            "refresh_token_expires_in": 90 * 86400,
        })

    # ------------------------------------------------------------------
    # Revoke endpoint
    # ------------------------------------------------------------------

    @http.route(
        "/oauth/revoke",
        type="http", auth="public", methods=["POST"], csrf=False,
    )
    def oauth_revoke(self, **_kw):
        form = request.httprequest.form
        token_value = form.get("token", "")

        if token_value:
            rec = request.env["mcp.token"].sudo().search(
                [("token", "=", token_value), ("state", "=", "active")],
                limit=1,
            )
            if rec:
                rec.action_revoke()

        # RFC 7009: always return 200 regardless of whether token existed
        return _json_resp({})
