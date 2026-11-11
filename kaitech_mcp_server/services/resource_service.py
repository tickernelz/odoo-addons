"""
MCP Resources — business context exposed as readable resources.

Claude reads these once at session start to understand the Odoo instance
without needing multiple tool calls to discover schemas and company info.

Resources:
  odoo://context              Company, user, installed modules, server date
  odoo://catalog              All installed models with names
  odoo://model/{name}         Field definitions for a specific model
  odoo://chatter/{model}/{id} Last 20 chatter messages for a record
  odoo://attachment/{id}      Attachment metadata + content preview
"""

import base64
import json
import logging
import re
from datetime import date

from . import schema_cache

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resource & template definitions (returned by resources/list)
# ---------------------------------------------------------------------------

RESOURCES = [
    {
        "uri": "odoo://context",
        "name": "Odoo Business Context",
        "description": (
            "Company info (name, currency, timezone), current user, "
            "list of installed modules, and server date. "
            "Always read this first before answering business questions."
        ),
        "mimeType": "application/json",
    },
    {
        "uri": "odoo://catalog",
        "name": "Odoo Model Catalog",
        "description": (
            "All available Odoo models with their technical name and display label. "
            "Use to find the correct model name before querying."
        ),
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "odoo://model/{name}",
        "name": "Odoo Model Schema",
        "description": (
            "Field definitions (label, type, required, selection values, related model) "
            "for any Odoo model. Read before using odoo_search_read or odoo_create "
            "to know which fields and values are valid."
        ),
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "odoo://chatter/{model}/{id}",
        "name": "Odoo Record Chatter",
        "description": (
            "Last 20 chatter messages for a specific record — comments, emails, "
            "internal notes, and activity logs. "
            "Read before replying to a customer or updating a record to see full history. "
            "Example: odoo://chatter/sale.order/201"
        ),
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "odoo://attachment/{id}",
        "name": "Odoo Attachment",
        "description": (
            "Metadata and content preview of an ir.attachment record. "
            "Text, JSON, XML, and CSV files under 50 KB include full content preview. "
            "Binary files (PDF, images) return metadata only with a download URL. "
            "Example: odoo://attachment/42"
        ),
        "mimeType": "application/json",
    },
]


# ---------------------------------------------------------------------------
# Resource dispatcher
# ---------------------------------------------------------------------------

def read_resource(uri: str, env) -> str:
    """Resolve a resource URI and return its JSON content as a string."""
    if uri == "odoo://context":
        return _context(env)
    if uri == "odoo://catalog":
        return _catalog(env)
    if uri.startswith("odoo://model/"):
        model_name = uri[len("odoo://model/"):]
        return _model_schema(model_name, env)
    if uri.startswith("odoo://chatter/"):
        rest = uri[len("odoo://chatter/"):]
        model_name, record_id = rest.rsplit("/", 1)
        return _chatter(model_name, record_id, env)
    if uri.startswith("odoo://attachment/"):
        attachment_id = uri[len("odoo://attachment/"):]
        return _attachment(attachment_id, env)
    raise ValueError(f"Unknown resource URI: {uri!r}")


# ---------------------------------------------------------------------------
# Resource implementations
# ---------------------------------------------------------------------------

def _context(env) -> str:
    company = env.company
    user = env.user

    installed = env["ir.module.module"].search_read(
        [("state", "=", "installed")],
        ["name", "shortdesc"],
        order="name",
        limit=500,
    )

    return json.dumps(
        {
            "company": {
                "name": company.name,
                "currency": company.currency_id.name,
                "country": company.country_id.name or "",
                "timezone": company.partner_id.tz or "UTC",
            },
            "user": {
                "id": user.id,
                "name": user.name,
                "login": user.login,
                "language": user.lang or "en_US",
            },
            "installed_modules": [
                {"name": m["name"], "label": m["shortdesc"]} for m in installed
            ],
            "server_date": str(date.today()),
        },
        ensure_ascii=False,
        indent=2,
    )


def _catalog(env) -> str:
    if schema_cache._cache_enabled(env):
        ttl = schema_cache._cache_ttl(env)

        def _compute():
            rows = env["ir.model"].search_read(
                [("transient", "=", False)],
                ["model", "name"],
                order="name",
                limit=1000,
            )
            return json.dumps({"total": len(rows), "models": rows}, ensure_ascii=False, indent=2)

        return schema_cache.get_or_compute("catalog", ttl, _compute)

    models = env["ir.model"].search_read(
        [("transient", "=", False)],
        ["model", "name"],
        order="name",
        limit=1000,
    )
    return json.dumps({"total": len(models), "models": models}, ensure_ascii=False, indent=2)


def _model_schema(model_name: str, env) -> str:
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    if schema_cache._cache_enabled(env):
        ttl = schema_cache._cache_ttl(env)
        cache_key = f"model_schema:{model_name}"

        def _compute():
            m = env[model_name]
            flds = m.fields_get(
                attributes=["string", "type", "required", "readonly", "help", "selection", "relation"]
            )
            return json.dumps(
                {"model": model_name, "fields": flds},
                ensure_ascii=False,
                default=str,
                indent=2,
            )

        return schema_cache.get_or_compute(cache_key, ttl, _compute)

    model = env[model_name]
    fields = model.fields_get(
        attributes=["string", "type", "required", "readonly", "help", "selection", "relation"]
    )
    return json.dumps(
        {"model": model_name, "fields": fields},
        ensure_ascii=False,
        default=str,
        indent=2,
    )


def _chatter(model_name: str, record_id: str, env) -> str:
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    rid = int(record_id)
    record = env[model_name].browse(rid)
    if not record.exists():
        raise ValueError(f"Record {model_name}#{rid} not found")

    if "mail.message" not in env.registry:
        raise ValueError("Module 'mail' is not installed — chatter not available")

    messages = env["mail.message"].search_read(
        [
            ("model", "=", model_name),
            ("res_id", "=", rid),
            ("message_type", "in", ["comment", "email", "email_outgoing", "notification"]),
        ],
        ["author_id", "date", "body", "subtype_id", "message_type", "attachment_ids"],
        order="date desc",
        limit=20,
    )

    cleaned = []
    for msg in messages:
        cleaned.append({
            "id": msg["id"],
            "author": msg["author_id"][1] if msg["author_id"] else "Unknown",
            "date": str(msg["date"]),
            "body": _strip_html(msg["body"])[:500],
            "type": msg["message_type"],
            "subtype": msg["subtype_id"][1] if msg["subtype_id"] else None,
            "attachment_ids": msg["attachment_ids"],
        })

    return json.dumps(
        {
            "model": model_name,
            "record_id": rid,
            "total_shown": len(cleaned),
            "note": "Showing last 20 messages. Older messages omitted.",
            "messages": cleaned,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _attachment(attachment_id: str, env) -> str:
    rec = env["ir.attachment"].browse(int(attachment_id))
    if not rec.exists():
        raise ValueError(f"Attachment {attachment_id} not found")

    result = {
        "id": rec.id,
        "name": rec.name,
        "mimetype": rec.mimetype or "application/octet-stream",
        "file_size": rec.file_size,
        "model": rec.res_model,
        "record_id": rec.res_id,
        "create_date": str(rec.create_date),
        "download_url": rec.url or f"/web/content/{rec.id}?download=1",
    }

    # Include content preview for readable text formats only
    _TEXT_MIMES = ("text/", "application/json", "application/xml",
                   "application/csv", "application/javascript")
    is_text = rec.mimetype and any(rec.mimetype.startswith(m) for m in _TEXT_MIMES)
    size_ok = rec.file_size and rec.file_size < 50 * 1024  # 50 KB limit

    if is_text and size_ok and rec.datas:
        try:
            raw = base64.b64decode(rec.datas).decode("utf-8", errors="replace")
            result["content_preview"] = raw[:5000]
            if len(raw) > 5000:
                result["content_truncated"] = True
        except Exception:
            result["content_preview"] = "[Could not decode attachment content]"
    elif is_text and not size_ok:
        result["content_preview"] = (
            f"[File too large ({rec.file_size} bytes) — download via download_url]"
        )
    else:
        result["content_preview"] = (
            f"[Binary file ({rec.mimetype}) — download via download_url]"
        )

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# HTML helper
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """Strip HTML tags for clean chatter body display."""
    if not html:
        return ""
    html = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = re.sub(r'&nbsp;', ' ', html)
    html = re.sub(r'&amp;', '&', html)
    html = re.sub(r'&lt;', '<', html)
    html = re.sub(r'&gt;', '>', html)
    html = re.sub(r'&quot;', '"', html)
    return re.sub(r'\s+', ' ', html).strip()
