# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
MCP tool definitions and handlers for portal page management.

These tools allow AI clients to create, list, and update shareable portal
pages that display live Odoo data via KPI tiles, charts, and tables.

Scope enforcement:
  - 'read'  → only odoo_list_portal_pages is permitted
  - 'write' → create + list + update
  - 'admin' → all (same as write for portal tools)

Handlers are called by the consolidation layer (tool_executor.py or its
replacement) via TOOL_HANDLERS dispatch table.
"""

import json
import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scope sets
# ---------------------------------------------------------------------------

_PORTAL_READ_TOOLS = frozenset({"odoo_list_portal_pages"})
_PORTAL_WRITE_TOOLS = frozenset({"odoo_create_portal_page", "odoo_update_portal_page"})

# ---------------------------------------------------------------------------
# Tool schema definitions (MCP tool manifest)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "odoo_create_portal_page",
        "description": (
            "Create a shareable portal page that displays live Odoo data via KPI tiles, "
            "charts, and tables. Returns the page URL and embed URL. "
            "The page is immediately accessible after creation. "
            "Use this when a user asks to 'create a dashboard', 'build a sales report page', "
            "'share a KPI view', or similar requests."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name", "spec"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display title for the portal page (e.g. 'Sales Dashboard Q1 2026').",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short description shown on the page.",
                },
                "is_public": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, anyone with the URL can view the page without logging in. "
                        "If false (default), viewers need an Odoo login or the embed token."
                    ),
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "auto"],
                    "default": "light",
                    "description": "Visual theme for the page.",
                },
                "spec": {
                    "type": "object",
                    "description": (
                        "Page layout specification. Must include a 'widgets' array. "
                        "Widget types: 'kpi', 'chart', 'table'. "
                        "Example: {\"title\": \"Revenue\", \"widgets\": [{\"type\": \"kpi\", "
                        "\"title\": \"Total Revenue\", \"model\": \"sale.order\", "
                        "\"domain\": [[\"state\",\"in\",[\"sale\",\"done\"]]], "
                        "\"field\": \"amount_total\", \"aggregate\": \"sum\", "
                        "\"format\": \"currency\"}]}"
                    ),
                    "properties": {
                        "title": {"type": "string"},
                        "subtitle": {"type": "string"},
                        "filters": {"type": "array"},
                        "widgets": {"type": "array"},
                    },
                },
            },
        },
    },
    {
        "name": "odoo_list_portal_pages",
        "description": (
            "List portal pages owned by the current user. "
            "Returns each page's ID, name, slug, URL, view count, and enabled status. "
            "Use this to find existing dashboards before creating new ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, only return enabled pages (default: true).",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of pages to return.",
                },
            },
        },
    },
    {
        "name": "odoo_update_portal_page",
        "description": (
            "Update an existing portal page's content or settings. "
            "Only the fields you provide will be changed — omitted fields are unchanged. "
            "Use the page 'id' from odoo_list_portal_pages to identify the page."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The Odoo record ID of the portal page to update.",
                },
                "name": {
                    "type": "string",
                    "description": "New display title.",
                },
                "description": {
                    "type": "string",
                    "description": "New description text.",
                },
                "spec": {
                    "type": "object",
                    "description": "Replacement page spec. Replaces the entire spec if provided.",
                },
                "is_public": {
                    "type": "boolean",
                    "description": "Update public access flag.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable or disable the page.",
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "auto"],
                    "description": "Update the visual theme.",
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


def _handle_create_portal_page(env, args: dict) -> dict:
    """Create a new mcp.portal.page record and return its URLs."""
    _check_scope(env, "odoo_create_portal_page")

    name = args.get("name", "").strip()
    if not name:
        raise ValueError("'name' is required and cannot be empty.")

    spec_obj = args.get("spec")
    if spec_obj is None:
        raise ValueError("'spec' is required.")
    if not isinstance(spec_obj, dict):
        raise ValueError("'spec' must be a JSON object.")

    spec_text = json.dumps(spec_obj, ensure_ascii=False, indent=2)

    # Validate spec via model method
    Page = env["mcp.portal.page"]
    Page.parse_spec(spec_text)

    vals = {
        "name": name,
        "spec": spec_text,
        "is_public": bool(args.get("is_public", False)),
        "theme": args.get("theme", "light"),
    }
    desc = args.get("description")
    if desc is not None:
        vals["description"] = desc

    page = Page.create(vals)

    base = env["ir.config_parameter"].sudo().get_param("web.base.url", "")
    return {
        "id": page.id,
        "name": page.name,
        "slug": page.slug,
        "url": f"{base}/mcp-page/{page.slug}",
        "embed_url": f"{base}/mcp-page/{page.slug}?token={page.access_token}",
        "is_public": page.is_public,
        "theme": page.theme,
        "message": (
            f"Portal page '{page.name}' created. "
            f"Share the URL with viewers: {base}/mcp-page/{page.slug}"
        ),
    }


def _handle_list_portal_pages(env, args: dict) -> dict:
    """Return a list of portal pages owned by the current user."""
    enabled_only = args.get("enabled_only", True)
    limit = min(int(args.get("limit", 20)), 100)

    domain = [("created_by", "=", env.uid)]
    if enabled_only:
        domain.append(("enabled", "=", True))

    pages = env["mcp.portal.page"].search(domain, limit=limit, order="create_date desc")
    base = env["ir.config_parameter"].sudo().get_param("web.base.url", "")

    result = []
    for p in pages:
        result.append({
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "url": f"{base}/mcp-page/{p.slug}",
            "is_public": p.is_public,
            "enabled": p.enabled,
            "view_count": p.view_count,
            "last_viewed": p.last_viewed.isoformat() if p.last_viewed else None,
            "theme": p.theme,
        })

    return {"count": len(result), "pages": result}


def _handle_update_portal_page(env, args: dict) -> dict:
    """Update fields on an existing portal page."""
    _check_scope(env, "odoo_update_portal_page")

    page_id = int(args.get("id", 0))
    if not page_id:
        raise ValueError("'id' (integer record ID) is required.")

    page = env["mcp.portal.page"].browse(page_id)
    if not page.exists():
        raise ValueError(f"Portal page with id={page_id} not found.")

    vals = {}
    if "name" in args:
        vals["name"] = args["name"]
    if "description" in args:
        vals["description"] = args["description"]
    if "is_public" in args:
        vals["is_public"] = bool(args["is_public"])
    if "enabled" in args:
        vals["enabled"] = bool(args["enabled"])
    if "theme" in args:
        vals["theme"] = args["theme"]
    if "spec" in args:
        spec_obj = args["spec"]
        if not isinstance(spec_obj, dict):
            raise ValueError("'spec' must be a JSON object.")
        spec_text = json.dumps(spec_obj, ensure_ascii=False, indent=2)
        env["mcp.portal.page"].parse_spec(spec_text)
        vals["spec"] = spec_text

    if not vals:
        return {"id": page_id, "message": "No fields to update were provided.", "updated": False}

    page.write(vals)

    base = env["ir.config_parameter"].sudo().get_param("web.base.url", "")
    return {
        "id": page.id,
        "name": page.name,
        "slug": page.slug,
        "url": f"{base}/mcp-page/{page.slug}",
        "updated": True,
        "message": f"Portal page '{page.name}' updated successfully.",
    }


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------


def _check_scope(env, tool_name: str) -> None:
    """Raise PermissionError if the current MCP scope does not allow *tool_name*."""
    scope = env.context.get("mcp_scope", "write")
    if scope == "admin" or scope == "write":
        return
    if scope == "read" and tool_name not in _PORTAL_READ_TOOLS:
        raise PermissionError(
            f"Tool {tool_name!r} requires at least 'write' scope. "
            f"This token is restricted to 'read' scope."
        )


# ---------------------------------------------------------------------------
# Dispatch table — consumed by the consolidation layer
# ---------------------------------------------------------------------------

TOOL_HANDLERS = {
    "odoo_create_portal_page": _handle_create_portal_page,
    "odoo_list_portal_pages": _handle_list_portal_pages,
    "odoo_update_portal_page": _handle_update_portal_page,
}
