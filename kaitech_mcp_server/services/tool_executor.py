import base64
import json
import logging

from markupsafe import Markup

from . import schema_cache
from . import bi_tools
from . import job_tools
from . import portal_tools
from . import artifact_tools
from . import module_tools
from .json_utils import odoo_json_default  # noqa: F401 — re-exported for callers

_logger = logging.getLogger(__name__)

# Tools allowed under 'read' scope — everything else is write-gated
_READ_ONLY_TOOLS = frozenset({
    # Core
    "odoo_get_models",
    "odoo_fields_get",
    "odoo_search_read",
    "odoo_count",
    "odoo_name_search",
    "odoo_read_group",
    "odoo_get_views",
    "odoo_default_get",
    "odoo_print_report",
    # BI tools — all read-only
    "odoo_pivot",
    "odoo_time_series",
    "odoo_top_n",
    "odoo_cohort",
    "odoo_funnel",
    "odoo_export_csv",
    "odoo_export_xlsx",
    # Job inspection
    "odoo_job_status",
    "odoo_job_list",
    # Portal read
    "odoo_list_portal_pages",
    # Artifact read
    "odoo_list_html_artifacts",
    "odoo_get_html_artifact",
    # Module generator read
    "odoo_validate_module_spec",
    "odoo_list_generated_modules",
    # Context tool (available to all scopes)
    "odoo_get_context",
})

# Tools blocked for 'write' scope (admin required)
_WRITE_SCOPE_BLOCKED_TOOLS = frozenset({
    "odoo_unlink",
    "odoo_call_method",   # call_method can do anything — admin only
    "odoo_generate_module",
    "odoo_install_generated_module",
})

# External tool handlers merged from satellite modules.
# module_tools uses a single dispatcher function (see _MODULE_TOOL_NAMES below).
_EXTERNAL_HANDLERS = {
    **bi_tools.TOOL_HANDLERS,
    **job_tools.TOOL_HANDLERS,
    **portal_tools.TOOL_HANDLERS,
    **artifact_tools.TOOL_HANDLERS,
}

_MODULE_TOOL_NAMES = frozenset(t["name"] for t in module_tools.TOOL_DEFINITIONS)


def execute_tool(env, tool_name: str, args: dict):
    """Dispatch a tool call to its implementation and return a serializable result.

    Scope enforcement is applied before dispatching. Scope is read from
    env.context['mcp_scope'] (set by the controller before calling process_message).
    Model/field restrictions are read from env.context['mcp_restrictions'].
    """
    _enforce_scope(env, tool_name)
    _enforce_model_access(env, tool_name, args)

    handlers = {
        "odoo_get_models": _get_models,
        "odoo_fields_get": _fields_get,
        "odoo_search_read": _search_read,
        "odoo_create": _create,
        "odoo_write": _write,
        "odoo_execute_wizard": _execute_wizard,
        "odoo_unlink": _unlink,
        "odoo_call_method": _call_method,
        # Analytics tools
        "odoo_count": _count,
        "odoo_name_search": _name_search,
        "odoo_read_group": _read_group,
        # Workflow & form tools
        "odoo_message_post": _message_post,
        "odoo_default_get": _default_get,
        "odoo_get_views": _get_views,
        "odoo_onchange": _onchange,
        "odoo_print_report": _print_report,
        "odoo_create_attachment": _create_attachment,
        # Context & catalog as tools — for clients that don't support MCP Resources (e.g. ChatGPT)
        "odoo_get_context": _get_context,
    }
    handler = handlers.get(tool_name)
    if handler:
        return handler(env, args)

    # Module generator tools share a single dispatcher (feature-flag + scope inside)
    if tool_name in _MODULE_TOOL_NAMES:
        return module_tools.execute_module_tool(env, tool_name, args)

    # BI / job / portal tools from satellite modules (imported directly by this module)
    external = _EXTERNAL_HANDLERS.get(tool_name)
    if external:
        return external(env, args)

    # Tools registered by satellite modules via mcp.tool.registry (_inherit-based
    # extension point — see McpToolRegistry.get_tool_handlers()).
    registered = env["mcp.tool.registry"].get_tool_handlers().get(tool_name)
    if registered:
        return registered(env, args)

    raise ValueError(f"Unknown tool: {tool_name!r}")


# ---------------------------------------------------------------------------
# Governance helpers
# ---------------------------------------------------------------------------


def _enforce_scope(env, tool_name: str) -> None:
    """Raise PermissionError if the current MCP scope does not permit *tool_name*."""
    scope = env.context.get("mcp_scope", "write")

    if scope == "admin":
        return  # admin may call everything

    registry = env["mcp.tool.registry"]

    if scope == "read":
        if tool_name not in _READ_ONLY_TOOLS and tool_name not in registry.get_read_only_tool_names():
            raise PermissionError(
                f"Tool {tool_name!r} requires at least 'write' scope. "
                f"This token is restricted to 'read' (read-only) scope. "
                f"Contact your administrator to upgrade the token scope."
            )
        return

    # scope == 'write' (default)
    if tool_name in _WRITE_SCOPE_BLOCKED_TOOLS or tool_name in registry.get_admin_only_tool_names():
        raise PermissionError(
            f"Tool {tool_name!r} requires 'admin' scope. "
            f"This token has 'write' scope. "
            f"Contact your administrator to upgrade the token scope."
        )


def _is_full_access(env) -> bool:
    """Token 'admin' (Full Access) tidak kena denylist method, larangan method
    privat, maupun batas jumlah id per panggilan."""
    return env.context.get("mcp_scope") == "admin"


def _enforce_model_access(env, tool_name: str, args: dict) -> None:
    """Raise PermissionError if the model in *args* is blocked by token restrictions."""
    restrictions = env.context.get("mcp_restrictions")
    if not restrictions:
        return

    model_name = args.get("model")
    if not model_name:
        return  # tools like odoo_get_models have no model arg

    denied_models = restrictions.get("denied_models") or set()
    allowed_models = restrictions.get("allowed_models") or set()

    if denied_models and model_name in denied_models:
        raise PermissionError(
            f"Access to model {model_name!r} is denied for this token."
        )
    if allowed_models and model_name not in allowed_models:
        raise PermissionError(
            f"Access to model {model_name!r} is not permitted by this token's model allowlist."
        )


def _get_field_restrictions(env, model_name: str) -> list | None:
    """Return allowed field list for *model_name* from context restrictions, or None."""
    restrictions = env.context.get("mcp_restrictions")
    if not restrictions:
        return None
    field_map = restrictions.get("field_restrictions") or {}
    return field_map.get(model_name)  # None if model not in map


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------


def _get_models(env, args: dict):
    filter_kw = args.get("filter", "") or ""
    cache_key = f"get_models:{filter_kw}"

    if schema_cache._cache_enabled(env):
        ttl = schema_cache._cache_ttl(env)

        def _compute():
            domain = [("transient", "=", False)]
            if filter_kw:
                domain.append(("model", "ilike", filter_kw))
            rows = env["ir.model"].search_read(
                domain, ["model", "name"], order="model", limit=500
            )
            return {"count": len(rows), "models": rows}

        return schema_cache.get_or_compute(cache_key, ttl, _compute)

    domain = [("transient", "=", False)]
    if filter_kw:
        domain.append(("model", "ilike", filter_kw))
    rows = env["ir.model"].search_read(
        domain,
        ["model", "name"],
        order="model",
        limit=500,
    )
    return {"count": len(rows), "models": rows}


def _fields_get(env, args: dict):
    model_name = args["model"]
    attributes = args.get("attributes") or ["string", "type", "required", "readonly", "help"]
    attr_key = ",".join(sorted(attributes))
    cache_key = f"fields_get:{model_name}:{attr_key}"

    if schema_cache._cache_enabled(env):
        ttl = schema_cache._cache_ttl(env)

        def _compute():
            m = _resolve_model(env, model_name)
            return m.fields_get(attributes=attributes)

        raw = schema_cache.get_or_compute(cache_key, ttl, _compute)
    else:
        raw = _resolve_model(env, model_name).fields_get(attributes=attributes)

    result = dict(raw)

    # Silently drop fields outside the allowlist (per-request restriction — never cached)
    allowed = _get_field_restrictions(env, model_name)
    if allowed is not None:
        result = {k: v for k, v in result.items() if k in allowed}

    return {"model": model_name, "fields": result}


def _search_read(env, args: dict):
    model_name = args["model"]
    domain = args.get("domain") or []
    fields = args.get("fields") or []
    limit = args.get("limit", 80)
    offset = args.get("offset", 0)
    order = args.get("order")

    # Silently intersect requested fields with allowlist
    allowed = _get_field_restrictions(env, model_name)
    if allowed is not None and fields:
        fields = [f for f in fields if f in allowed]
    elif allowed is not None and not fields:
        # No explicit field list — auto-restrict to allowlist only
        fields = list(allowed)

    model = _resolve_model(env, model_name)
    kw = {"limit": limit, "offset": offset}
    if order:
        kw["order"] = order

    records = model.search_read(domain, fields, **kw)
    total = model.search_count(domain)
    return {
        "model": model_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": records,
    }


def _create(env, args: dict):
    model_name = args["model"]
    values = args["values"]

    # Reject writes to fields outside the allowlist
    _check_write_fields(env, model_name, values)

    # Validate required fields before hitting the DB
    _check_required_fields(env, model_name, values)

    record = _resolve_model(env, model_name).create(values)
    return {"id": record.id, "model": model_name}


_MAX_IDS = 200  # safety cap: prevent AI from mass-updating/deleting in one call


def _write(env, args: dict):
    model_name = args["model"]
    ids = args["ids"]
    values = args["values"]
    if len(ids) > _MAX_IDS and not _is_full_access(env):
        raise ValueError(
            f"Too many records: {len(ids)} ids provided but maximum is {_MAX_IDS} per call. "
            f"Split into multiple calls or use a more specific domain."
        )

    # Reject writes to fields outside the allowlist
    _check_write_fields(env, model_name, values)

    _resolve_model(env, model_name).browse(ids).write(values)
    return {"updated": len(ids), "model": model_name, "ids": ids}


def _execute_wizard(env, args: dict):
    """Create a TransientModel wizard and execute a method on it atomically."""
    model_name = args["model"]
    values = args.get("values") or {}
    method_name = args["method"]
    method_kwargs = args.get("kwargs") or {}

    model = _resolve_model(env, model_name)

    if not model._transient:
        raise ValueError(
            f"Model {model_name!r} is not a TransientModel (wizard). "
            f"Use odoo_create + odoo_call_method for permanent models."
        )

    if method_name.startswith("_") and not _is_full_access(env):
        raise ValueError(
            f"Method {method_name!r} is private and cannot be called via MCP."
        )

    if not hasattr(model, method_name):
        available = sorted(
            m for m in dir(type(model))
            if not m.startswith("_")
            and callable(getattr(type(model), m, None))
            and m not in _CALL_METHOD_DENYLIST
        )[:15]
        raise ValueError(
            f"Method {method_name!r} not found on wizard {model_name!r}. "
            f"Available public methods: {available}"
        )

    wizard = model.create(values)
    result = getattr(wizard, method_name)(**method_kwargs)

    return {
        "wizard_id": wizard.id,
        "model": model_name,
        "method": method_name,
        "result": result,
    }


def _unlink(env, args: dict):
    model_name = args["model"]
    ids = args["ids"]
    if len(ids) > _MAX_IDS and not _is_full_access(env):
        raise ValueError(
            f"Too many records: {len(ids)} ids provided but maximum is {_MAX_IDS} per call. "
            f"Split into multiple calls or confirm each batch explicitly."
        )
    _resolve_model(env, model_name).browse(ids).unlink()
    return {"deleted": len(ids), "model": model_name, "ids": ids}


_CALL_METHOD_DENYLIST = frozenset({
    # Privilege escalation — would bypass ACL or switch execution context
    "sudo", "with_user", "with_company", "with_context", "with_env",
    # Raw DB access
    "execute", "execute_kw",
    # Use dedicated tools instead (odoo_search_read, odoo_create, etc.)
    "search", "search_read", "search_count", "read", "create", "write", "unlink",
    "read_group", "_read_group", "name_search", "name_get", "default_get",
    # ORM internals
    "browse", "exists", "ensure_one", "mapped", "filtered", "sorted", "invalidate_recordset",
})


def _call_method(env, args: dict):
    model_name = args["model"]
    method_name = args["method"]
    ids = args.get("ids") or []
    method_args = args.get("args") or []
    method_kwargs = args.get("kwargs") or {}

    full_access = _is_full_access(env)
    if method_name.startswith("_") and not full_access:
        raise ValueError(
            f"Method {method_name!r} is private (starts with '_') and cannot be called via MCP."
        )
    if method_name in _CALL_METHOD_DENYLIST and not full_access:
        raise ValueError(
            f"Method {method_name!r} is blocked. Use the dedicated MCP tool instead "
            f"(odoo_search_read, odoo_create, odoo_write, odoo_unlink, etc.)."
        )

    model = _resolve_model(env, model_name)
    if not hasattr(model, method_name):
        raise ValueError(f"Method {method_name!r} not found on model {model_name!r}")

    target = model.browse(ids) if ids else model
    result = getattr(target, method_name)(*method_args, **method_kwargs)
    return {"result": result}


_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def _create_attachment(env, args: dict):
    name = args["name"]
    content_b64 = args["content"]
    mimetype = args.get("mimetype", "application/octet-stream")
    res_model = args.get("model", "")
    res_id = int(args.get("record_id") or 0)

    try:
        raw = base64.b64decode(content_b64)
        file_size = len(raw)
    except Exception:
        raise ValueError("'content' must be valid base64-encoded data")

    if file_size > _MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"File too large: {file_size / 1_048_576:.1f} MB. "
            f"Maximum allowed size is {_MAX_ATTACHMENT_BYTES // 1_048_576} MB."
        )

    vals = {
        "name": name,
        "datas": content_b64,
        "mimetype": mimetype,
    }
    if res_model:
        if res_model not in env.registry:
            raise ValueError(f"Model {res_model!r} not found in Odoo registry")
        vals["res_model"] = res_model
        vals["res_id"] = res_id

    attachment = env["ir.attachment"].create(vals)
    return {
        "id": attachment.id,
        "name": attachment.name,
        "mimetype": attachment.mimetype,
        "file_size": file_size,
        "model": attachment.res_model or None,
        "record_id": attachment.res_id or None,
        "download_url": f"/web/content/{attachment.id}?download=1",
        "preview_uri": f"odoo://attachment/{attachment.id}",
    }


def _print_report(env, args: dict):
    model_name = args["model"]
    ids = args["ids"]
    report_ref = args.get("report", "")

    Report = env["ir.actions.report"]

    if report_ref:
        # Find by technical report_name (e.g. "sale.report_saleorder")
        report = Report.search([("report_name", "=", report_ref)], limit=1)
        if not report:
            # Fallback: try as XML ID
            try:
                report = env.ref(report_ref)
            except Exception:
                report = None
        if not report:
            raise ValueError(
                f"Report {report_ref!r} not found. "
                f"Use odoo_get_views(model='{model_name}') to see available reports, "
                f"or odoo_search_read(model='ir.actions.report', "
                f"domain=[['model','=','{model_name}']], fields=['name','report_name']) "
                f"for full list."
            )
    else:
        # Auto-detect: first PDF report for this model
        report = Report.search(
            [("model", "=", model_name), ("report_type", "in", ["qweb-pdf", "qweb-html"])],
            limit=1,
        )
        if not report:
            # Return all available reports so Claude can suggest one
            available = Report.search_read(
                [("model", "=", model_name)],
                ["name", "report_name", "report_type"],
            )
            return {
                "error": f"No PDF report found for {model_name!r}. Specify 'report' parameter.",
                "available_reports": available,
            }

    ids_str = ",".join(str(i) for i in ids)
    download_url = f"/report/pdf/{report.report_name}/{ids_str}"

    # Read record display names so Claude can confirm content to the user
    model_obj = _resolve_model(env, model_name)
    records = model_obj.browse(ids)
    record_names = [
        {"id": r.id, "name": r.display_name}
        for r in records if r.exists()
    ]

    # List other available reports for this model
    other_reports = Report.search_read(
        [("model", "=", model_name), ("id", "!=", report.id)],
        ["name", "report_name"],
        limit=10,
    )

    return {
        "model": model_name,
        "records": record_names,
        "report_name": report.report_name,
        "report_label": report.name,
        "download_url": download_url,
        "other_available_reports": other_reports,
        "note": (
            "Verify 'records' matches what the user expects before sharing the link. "
            "Open download_url in a browser with an active Odoo session to download."
        ),
    }


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


def _count(env, args: dict):
    model_name = args["model"]
    domain = args.get("domain") or []
    model = _resolve_model(env, model_name)
    total = model.search_count(domain)
    return {"model": model_name, "domain": domain, "count": total}


def _name_search(env, args: dict):
    model_name = args["model"]
    name = args.get("name", "")
    limit = args.get("limit", 10)
    model = _resolve_model(env, model_name)
    results = model.name_search(name, limit=limit)
    return {
        "model": model_name,
        "query": name,
        "results": [{"id": r[0], "display_name": r[1]} for r in results],
    }


def _read_group(env, args: dict):
    model_name = args["model"]
    domain = args.get("domain") or []
    groupby = args.get("groupby") or []
    fields = args.get("fields") or []
    limit = args.get("limit", 80)
    orderby = args.get("orderby")

    model = _resolve_model(env, model_name)
    kw = {"limit": limit}
    if orderby:
        kw["orderby"] = orderby

    rows = model._read_group(
        domain=domain,
        groupby=groupby,
        aggregates=fields,
        **kw,
    )

    # _read_group returns list of tuples — serialize to dicts
    def _serialize_group(row):
        result = {}
        for i, key in enumerate(groupby):
            field_name = key.split(":")[0]
            val = row[i]
            if hasattr(val, "id"):
                result[field_name] = {"id": val.id, "display_name": str(val)}
            else:
                result[field_name] = val
        for j, agg in enumerate(fields):
            field_name = agg.split(":")[0]
            result[field_name] = row[len(groupby) + j]
        return result

    return {
        "model": model_name,
        "groupby": groupby,
        "fields": fields,
        "rows": [_serialize_group(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# Workflow & form tools
# ---------------------------------------------------------------------------


def _message_post(env, args: dict):
    model_name = args["model"]
    record_id = args["id"]
    body = args["body"]
    subtype_xmlid = args.get("subtype", "mail.mt_comment")
    partner_ids = args.get("partner_ids") or []

    record = _resolve_model(env, model_name).browse(record_id)
    if not record.exists():
        raise ValueError(f"Record {model_name}#{record_id} not found")
    if not hasattr(record, "message_post"):
        raise ValueError(f"Model {model_name!r} does not support chatter (not mail.thread)")

    # Always escape the body through Odoo's sanitizer — never trust raw HTML
    # from an AI client. bleach/html_sanitize strips dangerous tags/attributes.
    from odoo.tools import html_sanitize
    if body and not body.strip().startswith("<"):
        # Plain text: wrap in <p> and let Odoo escape it normally
        safe_body = Markup(f"<p>{body}</p>")
    else:
        # HTML input: run through Odoo's sanitizer to strip XSS vectors
        safe_body = Markup(html_sanitize(body))

    msg = record.message_post(
        body=safe_body,
        subtype_xmlid=subtype_xmlid,
        partner_ids=partner_ids,
    )
    return {
        "message_id": msg.id,
        "model": model_name,
        "record_id": record_id,
        "author": msg.author_id.name,
        "date": str(msg.date),
    }


def _default_get(env, args: dict):
    model_name = args["model"]
    field_names = args.get("fields") or []

    model = _resolve_model(env, model_name)
    if not field_names:
        # Auto-discover scalar fields when no list given
        all_fields = model.fields_get(attributes=["type"])
        field_names = [
            fname for fname, fdef in all_fields.items()
            if fdef["type"] not in ("one2many", "binary")
        ][:60]

    defaults = model.default_get(field_names)

    # Serialize Many2one tuples/records
    serialized = {}
    for k, v in defaults.items():
        if isinstance(v, tuple):
            serialized[k] = {"id": v[0], "display_name": v[1]}
        elif hasattr(v, "id"):
            serialized[k] = {"id": v.id, "display_name": str(v)}
        else:
            serialized[k] = v

    return {
        "model": model_name,
        "defaults": serialized,
    }


def _get_views(env, args: dict):
    model_name = args["model"]
    cache_key = f"get_views:{model_name}"

    if schema_cache._cache_enabled(env):
        ttl = schema_cache._cache_ttl(env)

        def _compute():
            return _get_views_raw(env, model_name)

        return schema_cache.get_or_compute(cache_key, ttl, _compute)

    return _get_views_raw(env, model_name)


def _get_views_raw(env, model_name: str) -> dict:
    model = _resolve_model(env, model_name)

    # Walk MRO to collect action_* / button_* methods with their source class.
    seen: set = set()
    callable_methods = []
    for cls in type(model).__mro__:
        if cls is object:
            continue
        module = getattr(cls, "__module__", "") or ""
        if module.split(".")[0] in ("builtins", "abc", "functools", "threading", "logging"):
            continue
        for name, attr in cls.__dict__.items():
            if name in seen:
                continue
            if name.startswith(("action_", "button_")) and callable(attr):
                seen.add(name)
                callable_methods.append({
                    "name": name,
                    "defined_in": f"{module}.{cls.__qualname__}",
                })

    callable_methods.sort(key=lambda m: m["name"])

    bound_actions = env["ir.actions.server"].search_read(
        [("binding_model_id.model", "=", model_name)],
        ["name", "state", "binding_type"],
        limit=50,
    )

    window_actions = env["ir.actions.act_window"].search_read(
        [("res_model", "=", model_name)],
        ["name", "view_mode"],
        limit=20,
    )

    reports = env["ir.actions.report"].search_read(
        [("model", "=", model_name)],
        ["name", "report_name", "report_type"],
        limit=20,
    )

    return {
        "model": model_name,
        "callable_methods": callable_methods,
        "action_menu_items": [
            {"name": a["name"], "type": a["state"], "binding": a["binding_type"]}
            for a in bound_actions
        ],
        "window_actions": [
            {"name": a["name"], "view_modes": a["view_mode"]}
            for a in window_actions
        ],
        "available_reports": [
            {"name": r["name"], "report_name": r["report_name"], "type": r["report_type"]}
            for r in reports
        ],
    }


def _onchange(env, args: dict):
    model_name = args["model"]
    values = args.get("values") or {}
    field_onchange = args.get("field_onchange") or []

    model = _resolve_model(env, model_name)

    # Odoo onchange spec: {field: "1"} for each field to trigger
    onchange_spec = {f: "1" for f in field_onchange}
    result = model.onchange(values, field_onchange, onchange_spec)

    # Serialize result — skip one2many command lists to avoid context explosion
    raw_values = result.get("value", {})
    computed = {}
    for key, val in raw_values.items():
        if isinstance(val, list):
            computed[key] = f"[{len(val)} one2many records — use odoo_search_read to read]"
        elif isinstance(val, tuple) and len(val) == 2:
            computed[key] = {"id": val[0], "display_name": val[1]}
        elif hasattr(val, "id"):
            computed[key] = {"id": val.id, "display_name": str(val)}
        else:
            computed[key] = val

    return {
        "model": model_name,
        "triggered_fields": field_onchange,
        "computed_values": computed,
        "domain_updates": result.get("domain", {}),
        "warning": result.get("warning"),
    }


# ---------------------------------------------------------------------------
# Context & catalog tools (for clients that don't support MCP Resources)
# ---------------------------------------------------------------------------


def _get_context(env, _args: dict):
    """Return Odoo business context as a dict (same data as odoo://context resource)."""
    from . import resource_service
    return {"context": json.loads(resource_service._context(env))}


# ---------------------------------------------------------------------------
# Write field restriction helper
# ---------------------------------------------------------------------------


def _check_write_fields(env, model_name: str, values: dict) -> None:
    """Raise PermissionError if *values* contains fields outside the token's field allowlist."""
    allowed = _get_field_restrictions(env, model_name)
    if allowed is None:
        return  # no restriction
    blocked = [k for k in values if k not in allowed]
    if blocked:
        raise PermissionError(
            f"Write to field(s) {blocked!r} on model {model_name!r} is not permitted "
            f"by this token's field restrictions."
        )


def _check_required_fields(env, model_name: str, values: dict) -> None:
    """Raise ValueError listing required fields missing from *values* with no runtime default.

    Calls default_get() so fields that have a server-side default (e.g. sequences,
    current user) are not flagged as missing.
    """
    model = _resolve_model(env, model_name)
    field_defs = model.fields_get(attributes=["required"])
    required_names = [f for f, d in field_defs.items() if d.get("required")]

    if not required_names:
        return

    provided = set(values)

    try:
        defaults = model.default_get(required_names)
        has_default = set(defaults)
    except Exception:
        has_default = set()

    missing = [f for f in required_names if f not in provided and f not in has_default]
    if missing:
        raise ValueError(
            f"Missing required field(s) for model {model_name!r}: {missing}. "
            f"Call odoo_fields_get(model='{model_name}', "
            f"attributes=['required', 'string', 'relation']) to inspect the schema, "
            f"then retry with the missing fields included."
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _resolve_model(env, model_name: str):
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")
    return env[model_name]
