# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
Public HTML artifact controller.

Routes:
  GET /mcp-artifact/<slug>        — host page with sandboxed iframe (full chrome)
  GET /mcp-artifact/<slug>/embed  — host page with sandboxed iframe (no chrome)
  GET /mcp-artifact/<slug>/raw    — raw sandboxed document (intended for iframe srcdoc only)

The artifact body is assembled server-side from the html / css / js fields and
served inside an iframe declared with ``sandbox="allow-scripts"`` (no
``allow-same-origin``). A strict Content-Security-Policy meta tag is injected
to limit script-src / style-src to a small set of trusted CDNs and to block
``connect-src``.
"""

import hmac
import html as html_lib
import logging

from markupsafe import Markup

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


_CSP_POLICY = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com "
    "https://cdnjs.cloudflare.com; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com "
    "https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com data:; "
    "img-src data: blob: https:; "
    "media-src data: blob: https:; "
    "connect-src 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def _build_sandbox_document(artifact) -> str:
    """Assemble the inner sandboxed HTML document for an artifact."""
    title = html_lib.escape(artifact.name or "Artifact")
    css = artifact.css or ""
    js = artifact.js or ""
    body_html = artifact.html or ""
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{_CSP_POLICY}\"/>\n"
        f"<title>{title}</title>\n"
        "<style>\n"
        "html,body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"
        "'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;}\n"
        f"{css}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html}\n"
        f"<script>{js}</script>\n"
        "</body>\n"
        "</html>\n"
    )


class McpArtifactController(http.Controller):

    @http.route(
        "/mcp-artifact/<string:slug>",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def artifact_page(self, slug, **kwargs):
        return self._render_host(slug, kwargs, embed=False)

    @http.route(
        "/mcp-artifact/<string:slug>/embed",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def artifact_embed(self, slug, **kwargs):
        return self._render_host(slug, kwargs, embed=True)

    @http.route(
        "/mcp-artifact/<string:slug>/raw",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def artifact_raw(self, slug, **kwargs):
        """Serve the inner sandboxed document directly.

        Intended for advanced embedding cases. The iframe-in-host approach is
        preferred (see ``artifact_page``) because it lets us declare the sandbox
        attribute on the iframe.
        """
        art = self._get_artifact_or_404(slug)
        if art is None:
            return request.not_found()

        auth_result = self._check_auth(art, kwargs.get("token", ""))
        if auth_result is not None:
            return auth_result

        self._bump_view_counter(art)
        body = _build_sandbox_document(art)
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Security-Policy", _CSP_POLICY),
            ("X-Frame-Options", "SAMEORIGIN"),
        ]
        return request.make_response(body, headers=headers)

    def _render_host(self, slug, kwargs, embed: bool):
        art = self._get_artifact_or_404(slug)
        if art is None:
            return request.not_found()

        auth_result = self._check_auth(art, kwargs.get("token", ""))
        if auth_result is not None:
            return auth_result

        self._bump_view_counter(art)
        srcdoc = _build_sandbox_document(art)
        qcontext = {
            "artifact": art,
            "srcdoc": Markup(html_lib.escape(srcdoc, quote=True)),
            "embed": embed,
            "token": kwargs.get("token", ""),
        }
        headers = {"X-Frame-Options": "ALLOWALL" if embed else "SAMEORIGIN"}
        return request.render("kaitech_mcp_server.artifact_page", qcontext, headers=headers)

    def _get_artifact_or_404(self, slug: str):
        art = request.env["mcp.html.artifact"].sudo().search(
            [("slug", "=", slug), ("enabled", "=", True)],
            limit=1,
        )
        return art or None

    def _check_auth(self, art, token: str):
        """None when the artifact may be shown: public, a valid token, or readable to the logged-in user."""
        if art.is_public or (token and art.access_token and hmac.compare_digest(token, art.access_token)):
            return None
        uid = request.session.uid if request.session else None
        if not uid:
            return request.redirect(f"/web/login?redirect=/mcp-artifact/{art.slug}", code=302)
        if not request.env["mcp.html.artifact"].with_user(uid).search_count([("id", "=", art.id)]):
            return request.not_found()
        return None

    def _bump_view_counter(self, art):
        from odoo import fields
        art.sudo().write({
            "view_count": art.view_count + 1,
            "last_viewed": fields.Datetime.now(),
        })
