# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
MCP tool definitions and handlers for HTML artifact management.

Artifacts are free-form interactive HTML snippets (HTML + optional CSS + JS)
served through a strictly sandboxed iframe. They differ from portal pages:
portal pages are spec-driven dashboards over live Odoo data, while artifacts
are opaque AI-generated markup (visualizations, mini-apps, prototypes).

Scope enforcement:
  - 'read'  → only odoo_list_html_artifacts, odoo_get_html_artifact
  - 'write' → create + list + get + update
  - 'admin' → all (same as write for artifact tools)
"""

import logging

_logger = logging.getLogger(__name__)


_ARTIFACT_READ_TOOLS = frozenset({"odoo_list_html_artifacts", "odoo_get_html_artifact"})
_ARTIFACT_WRITE_TOOLS = frozenset({"odoo_create_html_artifact", "odoo_update_html_artifact"})


TOOL_DEFINITIONS = [
    {
        "name": "odoo_create_html_artifact",
        "description": (
            "Create an interactive HTML artifact rendered inside a strictly sandboxed iframe. "
            "Use this when a user asks to 'create a mini-app', 'render a chart', 'build a "
            "calculator', 'show a visualization', 'make an interactive demo', or wants any "
            "free-form interactive HTML page. Returns the public URL and embed URL. "
            "Security: the artifact runs with no access to the parent Odoo page (no cookies, "
            "no DOM access). Outbound network is blocked. Allowed CDNs for scripts/styles: "
            "cdn.jsdelivr.net, unpkg.com, cdnjs.cloudflare.com, fonts.googleapis.com."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name", "html"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display title for the artifact (e.g. 'Tip Calculator').",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short description.",
                },
                "html": {
                    "type": "string",
                    "description": (
                        "HTML markup that will be placed inside the <body> of the sandboxed "
                        "document. May include inline <style> and <script> tags."
                    ),
                },
                "css": {
                    "type": "string",
                    "description": "Optional CSS injected into a <style> tag in <head>.",
                },
                "js": {
                    "type": "string",
                    "description": (
                        "Optional JS injected into a <script> tag at the end of <body>. "
                        "Runs inside the sandbox with no access to the parent window."
                    ),
                },
                "is_public": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, anyone with the URL can view the artifact without logging in."
                    ),
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "auto"],
                    "default": "light",
                    "description": "Visual theme for the host page surrounding the iframe.",
                },
            },
        },
    },
    {
        "name": "odoo_list_html_artifacts",
        "description": (
            "List HTML artifacts owned by the current user. Returns each artifact's ID, name, "
            "slug, URL, view count, and enabled status. Use this to find existing artifacts "
            "before creating new ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, only return enabled artifacts.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of artifacts to return.",
                },
            },
        },
    },
    {
        "name": "odoo_get_html_artifact",
        "description": (
            "Return the full content (html, css, js) of an HTML artifact by id or slug. "
            "Use this to inspect or modify an existing artifact before calling update."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Artifact record ID."},
                "slug": {"type": "string", "description": "Artifact slug."},
            },
        },
    },
    {
        "name": "odoo_update_html_artifact",
        "description": (
            "Update fields on an existing HTML artifact. Only the fields you provide are "
            "changed — omitted fields are unchanged. Use the 'id' from odoo_list_html_artifacts."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "integer", "description": "The artifact record ID."},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "html": {"type": "string"},
                "css": {"type": "string"},
                "js": {"type": "string"},
                "is_public": {"type": "boolean"},
                "enabled": {"type": "boolean"},
                "theme": {"type": "string", "enum": ["light", "dark", "auto"]},
            },
        },
    },
]


def _check_scope(env, tool_name: str) -> None:
    scope = env.context.get("mcp_scope", "write")
    if scope in ("admin", "write"):
        return
    if scope == "read" and tool_name not in _ARTIFACT_READ_TOOLS:
        raise PermissionError(
            f"Tool {tool_name!r} requires at least 'write' scope. "
            f"This token is restricted to 'read' scope."
        )


def _base_url(env) -> str:
    return env["ir.config_parameter"].sudo().get_param("web.base.url", "")


def _serialize(env, art) -> dict:
    base = _base_url(env)
    return {
        "id": art.id,
        "name": art.name,
        "slug": art.slug,
        "url": f"{base}/mcp-artifact/{art.slug}",
        "embed_url": f"{base}/mcp-artifact/{art.slug}?token={art.access_token}",
        "is_public": art.is_public,
        "enabled": art.enabled,
        "theme": art.theme,
        "view_count": art.view_count,
        "last_viewed": art.last_viewed.isoformat() if art.last_viewed else None,
    }


def _handle_create_html_artifact(env, args: dict) -> dict:
    _check_scope(env, "odoo_create_html_artifact")

    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("'name' is required and cannot be empty.")

    html = args.get("html")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("'html' is required and must be a non-empty string.")

    vals = {
        "name": name,
        "html": html,
        "css": args.get("css") or False,
        "js": args.get("js") or False,
        "is_public": bool(args.get("is_public", False)),
        "theme": args.get("theme", "light"),
    }
    desc = args.get("description")
    if desc is not None:
        vals["description"] = desc

    art = env["mcp.html.artifact"].create(vals)
    result = _serialize(env, art)
    result["message"] = (
        f"HTML artifact '{art.name}' created. Share the URL with viewers: {result['url']}"
    )
    return result


def _handle_list_html_artifacts(env, args: dict) -> dict:
    enabled_only = args.get("enabled_only", True)
    limit = min(int(args.get("limit", 20)), 100)

    domain = [("created_by", "=", env.uid)]
    if enabled_only:
        domain.append(("enabled", "=", True))

    arts = env["mcp.html.artifact"].search(domain, limit=limit, order="create_date desc")
    return {"count": len(arts), "artifacts": [_serialize(env, a) for a in arts]}


def _handle_get_html_artifact(env, args: dict) -> dict:
    art_id = args.get("id")
    slug = args.get("slug")
    if not art_id and not slug:
        raise ValueError("Either 'id' or 'slug' must be provided.")

    Artifact = env["mcp.html.artifact"]
    if art_id:
        art = Artifact.browse(int(art_id))
        if not art.exists():
            raise ValueError(f"HTML artifact with id={art_id} not found.")
    else:
        art = Artifact.search([("slug", "=", slug)], limit=1)
        if not art:
            raise ValueError(f"HTML artifact with slug={slug!r} not found.")

    if art.created_by.id != env.uid and not env.user.has_group("base.group_system"):
        raise PermissionError("You do not own this artifact.")

    payload = _serialize(env, art)
    payload.update({
        "description": art.description or "",
        "html": art.html or "",
        "css": art.css or "",
        "js": art.js or "",
    })
    return payload


def _handle_update_html_artifact(env, args: dict) -> dict:
    _check_scope(env, "odoo_update_html_artifact")

    art_id = int(args.get("id", 0))
    if not art_id:
        raise ValueError("'id' (integer record ID) is required.")

    art = env["mcp.html.artifact"].browse(art_id)
    if not art.exists():
        raise ValueError(f"HTML artifact with id={art_id} not found.")

    vals = {}
    for key in ("name", "description", "html", "css", "js", "theme"):
        if key in args:
            vals[key] = args[key]
    for key in ("is_public", "enabled"):
        if key in args:
            vals[key] = bool(args[key])

    if not vals:
        return {"id": art_id, "message": "No fields to update were provided.", "updated": False}

    art.write(vals)
    result = _serialize(env, art)
    result["updated"] = True
    result["message"] = f"HTML artifact '{art.name}' updated successfully."
    return result


TOOL_HANDLERS = {
    "odoo_create_html_artifact": _handle_create_html_artifact,
    "odoo_list_html_artifacts": _handle_list_html_artifacts,
    "odoo_get_html_artifact": _handle_get_html_artifact,
    "odoo_update_html_artifact": _handle_update_html_artifact,
}
