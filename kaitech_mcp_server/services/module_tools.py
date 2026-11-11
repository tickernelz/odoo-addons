"""
MCP Module Generator Tool Handlers
====================================

Defines the four MCP tools for AI-driven module generation:

- odoo_generate_module      (admin scope required)
- odoo_validate_module_spec (any scope)
- odoo_list_generated_modules (any scope)
- odoo_install_generated_module (admin scope required)

Scope enforcement mirrors tool_executor._enforce_scope pattern:
read env.context['mcp_scope'] and raise PermissionError when the
operation requires a higher scope than the token grants.

Feature flag: if ir.config_parameter 'mcp_server.enable_module_generator'
is falsy, all four handlers return an error dict instead of performing
the operation.
"""

import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (advertised to AI clients via tools/list)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "odoo_generate_module",
        "description": (
            "Generate an installable Odoo 19 module from a JSON specification. "
            "Returns a download URL for the ZIP archive. "
            "REQUIRES admin scope. "
            "Call odoo_validate_module_spec first to check the spec for errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": (
                        "Module specification object. Required keys: technical_name, name. "
                        "Optional: summary, description, category, version, depends, license, "
                        "models (list), menus (list), security (object), "
                        "views.auto_generate (bool), demo_data (list)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the generated module record.",
                },
            },
            "required": ["spec"],
        },
    },
    {
        "name": "odoo_validate_module_spec",
        "description": (
            "Validate an Odoo module specification without generating anything. "
            "Returns a list of validation errors (empty list = valid). "
            "Use this before odoo_generate_module to catch mistakes early."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": "Module specification object (same format as odoo_generate_module).",
                },
            },
            "required": ["spec"],
        },
    },
    {
        "name": "odoo_list_generated_modules",
        "description": (
            "List Odoo modules previously generated via odoo_generate_module, "
            "with their state (draft, generated, installed, failed). "
            "Returns the most recent modules first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of records to return.",
                    "default": 20,
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: draft, generated, installed, failed. Leave empty for all.",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "odoo_install_generated_module",
        "description": (
            "Install a previously generated module on this Odoo instance. "
            "Extracts the ZIP to the configured addons path and runs module installation. "
            "REQUIRES admin scope and explicit confirmation parameter set to true. "
            "WARNING: This restarts the Odoo module registry. Use with care."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {
                    "type": "integer",
                    "description": "ID of the mcp.generated.module record to install.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to proceed. Prevents accidental installs.",
                    "default": False,
                },
            },
            "required": ["module_id", "confirm"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers (called by execute_module_tool)
# ---------------------------------------------------------------------------


def execute_module_tool(env, tool_name: str, args: dict):
    """Dispatch a module-generator tool call.

    Called from tool_executor.execute_tool after it delegates module tools here.
    Scope enforcement and feature flag check are performed here.
    """
    if not _feature_enabled(env):
        return {
            "error": "Module generator is disabled by administrator. "
                     "Enable it in MCP Server settings (enable_module_generator)."
        }

    _enforce_module_scope(env, tool_name)

    handlers = {
        "odoo_generate_module": _handle_generate,
        "odoo_validate_module_spec": _handle_validate,
        "odoo_list_generated_modules": _handle_list,
        "odoo_install_generated_module": _handle_install,
    }
    handler = handlers.get(tool_name)
    if not handler:
        raise ValueError(f"Unknown module tool: {tool_name!r}")
    return handler(env, args)


# ---------------------------------------------------------------------------
# Feature flag helper
# ---------------------------------------------------------------------------


def _feature_enabled(env) -> bool:
    param = env["ir.config_parameter"].sudo().get_param(
        "mcp_server.enable_module_generator", "False"
    )
    return param in ("True", "1", "true", "yes")


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------

_ADMIN_ONLY_TOOLS = frozenset({
    "odoo_generate_module",
    "odoo_install_generated_module",
})


def _enforce_module_scope(env, tool_name: str) -> None:
    scope = env.context.get("mcp_scope", "write")
    if tool_name in _ADMIN_ONLY_TOOLS and scope != "admin":
        raise PermissionError(
            f"Tool {tool_name!r} requires 'admin' scope. "
            f"Current token scope is {scope!r}. "
            f"Contact your administrator to upgrade the token scope."
        )


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------


def _handle_generate(env, args: dict) -> dict:
    spec = args.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("'spec' must be a JSON object (dict).")

    display_name = args.get("name") or spec.get("name") or spec.get("technical_name", "Generated Module")

    rec = env["mcp.generated.module"].sudo().create({
        "name": display_name,
        "technical_name": spec.get("technical_name", ""),
        "summary": spec.get("summary", ""),
        "description": spec.get("description", ""),
        "category": spec.get("category", "Productivity"),
        "version": spec.get("version", "19.0.1.0.0"),
        "license": spec.get("license", "LGPL-3"),
        "depends": (
            ",".join(spec["depends"])
            if isinstance(spec.get("depends"), list)
            else spec.get("depends", "base")
        ),
        "spec": _safe_json_dumps(spec),
        "created_by": env.uid,
    })

    try:
        rec.action_generate()
    except Exception as exc:
        return {
            "error": str(exc),
            "module_id": rec.id,
            "state": "failed",
        }

    result = {
        "module_id": rec.id,
        "technical_name": rec.technical_name,
        "state": rec.state,
        "generation_log": rec.generation_log or "",
    }
    if rec.attachment_id:
        result["download_url"] = (
            f"/web/content/{rec.attachment_id.id}?download=1"
        )

    return result


def _handle_validate(env, args: dict) -> dict:
    spec = args.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("'spec' must be a JSON object (dict).")

    from . import module_generator
    errors = module_generator.validate_spec(spec)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "error_count": len(errors),
    }


def _handle_list(env, args: dict) -> dict:
    limit = int(args.get("limit") or 20)
    state_filter = args.get("state") or ""

    domain = []
    if state_filter:
        domain.append(("state", "=", state_filter))

    recs = env["mcp.generated.module"].sudo().search_read(
        domain,
        ["name", "technical_name", "version", "state", "create_date",
         "created_by", "attachment_id"],
        limit=limit,
        order="create_date desc",
    )

    # Enrich with download URLs
    for rec in recs:
        att = rec.get("attachment_id")
        if att and att[0]:
            rec["download_url"] = f"/web/content/{att[0]}?download=1"
        else:
            rec["download_url"] = None

    return {
        "count": len(recs),
        "modules": recs,
    }


def _handle_install(env, args: dict) -> dict:
    module_id = args.get("module_id")
    confirm = args.get("confirm", False)

    if not confirm:
        return {
            "error": "Set 'confirm': true to proceed with installation. "
                     "This operation restarts the Odoo module registry."
        }

    if not module_id:
        raise ValueError("'module_id' is required.")

    rec = env["mcp.generated.module"].sudo().browse(int(module_id))
    if not rec.exists():
        raise ValueError(f"mcp.generated.module#{module_id} not found.")

    try:
        rec.action_install()
    except Exception as exc:
        return {
            "error": str(exc),
            "module_id": rec.id,
            "state": rec.state,
        }

    return {
        "module_id": rec.id,
        "technical_name": rec.technical_name,
        "state": rec.state,
        "installed_at": str(rec.installed_at) if rec.installed_at else None,
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _safe_json_dumps(obj) -> str:
    import json
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)
