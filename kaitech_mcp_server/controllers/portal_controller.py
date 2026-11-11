# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
Public portal page controller.

Routes:
  GET /mcp-page/<slug>        — full page render (HTML)
  GET /mcp-page/<slug>/data   — live widget data (JSON)
  GET /mcp-page/<slug>/embed  — iframe-embeddable render (no Odoo chrome)

Authentication rules (applied in order):
  1. If page.is_public → allow everyone.
  2. If request has ?token=<access_token> matching the page → allow.
  3. If the user has an active Odoo session (request.session.uid) → allow.
  4. Otherwise → redirect to /web/login.

Widget data is always computed using page.created_by as the execution user,
restricting data access to what the page owner can see. This prevents a
public page from leaking data the viewing user should not see.
"""

import json
import logging

from markupsafe import Markup
from werkzeug.wrappers import Response

from odoo import http
from odoo.http import request

from ..services.portal_renderer import compute_widget

_logger = logging.getLogger(__name__)


class McpPortalController(http.Controller):

    # ------------------------------------------------------------------
    # Main page render
    # ------------------------------------------------------------------

    @http.route(
        "/mcp-page/<string:slug>",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def portal_page(self, slug, **kwargs):
        page = self._get_page_or_404(slug)
        if page is None:
            return request.not_found()

        auth_result = self._check_auth(page, kwargs.get("token", ""))
        if auth_result is not None:
            return auth_result

        widget_data = self._render_widgets(page, kwargs)
        spec = self._parse_spec_safe(page)
        page.sudo().write({
            "view_count": page.view_count + 1,
            "last_viewed": fields_now(),
        })

        qcontext = self._build_qcontext(page, spec, widget_data, kwargs.get("token", ""), embed=False)
        return request.render(
            "kaitech_mcp_server.portal_page",
            qcontext,
            headers={"X-Frame-Options": "SAMEORIGIN"},
        )

    # ------------------------------------------------------------------
    # Embed route (no Odoo chrome — for iframes)
    # ------------------------------------------------------------------

    @http.route(
        "/mcp-page/<string:slug>/embed",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def portal_page_embed(self, slug, **kwargs):
        page = self._get_page_or_404(slug)
        if page is None:
            return request.not_found()

        auth_result = self._check_auth(page, kwargs.get("token", ""))
        if auth_result is not None:
            return auth_result

        widget_data = self._render_widgets(page, kwargs)
        spec = self._parse_spec_safe(page)

        qcontext = self._build_qcontext(page, spec, widget_data, kwargs.get("token", ""), embed=True)
        return request.render(
            "kaitech_mcp_server.portal_page",
            qcontext,
            headers={"X-Frame-Options": "ALLOWALL"},
        )

    # ------------------------------------------------------------------
    # Live JSON data endpoint
    # ------------------------------------------------------------------

    @http.route(
        "/mcp-page/<string:slug>/data",
        type="http",
        auth="public",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def portal_page_data(self, slug, **kwargs):
        page = self._get_page_or_404(slug)
        if page is None:
            return _json_error("not_found", "Page not found.", 404)

        auth_result = self._check_auth(page, kwargs.get("token", ""))
        if auth_result is not None:
            return _json_error("unauthorized", "Authentication required.", 401)

        widget_data = self._render_widgets(page, kwargs)
        payload = json.dumps(
            {"slug": slug, "widgets": widget_data},
            default=_json_default,
            ensure_ascii=False,
        )
        return Response(
            payload,
            content_type="application/json; charset=utf-8",
            status=200,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_page_or_404(self, slug: str):
        """Return the mcp.portal.page record for *slug*, or None."""
        page = request.env["mcp.portal.page"].sudo().search(
            [("slug", "=", slug), ("enabled", "=", True)],
            limit=1,
        )
        return page or None

    def _check_auth(self, page, token: str):
        """Return a redirect/error response if access is denied, else None."""
        if page.is_public:
            return None
        if token and token == page.access_token:
            return None
        if request.session and request.session.uid:
            return None
        # Redirect to login, preserving return URL
        login_url = f"/web/login?redirect=/mcp-page/{page.slug}"
        if token:
            login_url += f"?token={token}"
        return request.redirect(login_url, code=302)

    def _render_widgets(self, page, filter_values: dict) -> list:
        """Compute render data for every widget in the page spec."""
        spec = self._parse_spec_safe(page)
        widgets_spec = spec.get("widgets") or []

        # Compute filter defaults merged with query-string values
        resolved_filters = {}
        for f in spec.get("filters") or []:
            name = f.get("name", "")
            default = f.get("default", "")
            resolved_filters[name] = filter_values.get(name, default)

        # Run as created_by user — governance boundary
        env_as_owner = request.env["mcp.portal.page"].with_user(page.created_by.id).env

        result = []
        for ws in widgets_spec:
            data = compute_widget(env_as_owner, ws, resolved_filters)
            result.append(data)
        return result

    def _build_qcontext(self, page, spec: dict, widget_data: list, token: str, embed: bool) -> dict:
        """Build the template context, including pre-serialized ECharts option JSON."""
        charts_data = []
        for idx, w in enumerate(widget_data):
            if w.get("type") == "chart" and not w.get("error") and w.get("echarts_option"):
                charts_data.append({
                    "index": idx,
                    "option": w["echarts_option"],
                })
        return {
            "page": page,
            "spec": spec,
            "widgets": widget_data,
            "embed": embed,
            "token": token,
            "json_charts_data": Markup(json.dumps(charts_data, default=_json_default, ensure_ascii=False)),
        }

    def _parse_spec_safe(self, page) -> dict:
        """Parse page.spec JSON, returning {} on error."""
        try:
            return json.loads(page.spec or "{}")
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _json_error(code: str, message: str, status: int) -> Response:
    body = json.dumps({"error": code, "message": message}, ensure_ascii=False)
    return Response(body, content_type="application/json; charset=utf-8", status=status)


def _json_default(obj):
    """Fallback serializer for non-JSON-native types from ORM."""
    import datetime
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)


def fields_now():
    """Return current datetime via Odoo fields (avoids direct import at module level)."""
    from odoo import fields
    return fields.Datetime.now()
