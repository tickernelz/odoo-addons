"""
MCP async job runner — execution logic for each operation type.

Each public function receives:
  env     — Odoo Environment (already has mcp_scope + mcp_restrictions in context)
  args    — dict of operation-specific arguments
  job     — mcp.job recordset (single record) for incremental progress updates

All functions return a dict that will be JSON-serialised and stored in job.result.
Per-record errors are accumulated rather than aborting the whole batch.
"""
import csv
import io
import json
import logging

from odoo import fields

_logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Progress helper
# ---------------------------------------------------------------------------

def _update_progress(job, processed: int, total: int):
    """Write progress percentage back to the job record and commit.

    Commit is intentional here — long-running jobs must release row locks
    in the batch table and make intermediate progress visible to polling clients.
    Acceptable in cron context per CLAUDE.md guidelines.
    """
    if total > 0:
        pct = min(99, int(processed / total * 100))
    else:
        pct = 0
    try:
        job.write({"progress": pct, "processed_records": processed, "total_records": total})
        job.env.cr.commit()
    except Exception:
        _logger.debug("MCP job_runner: could not update progress for job %s", job.id)


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------

def run_bulk_update(env, args: dict, job) -> dict:
    """args: {model, domain, values, batch_size=100}"""
    model_name = args.get("model")
    domain = args.get("domain") or []
    values = args.get("values") or {}
    batch_size = int(args.get("batch_size") or _DEFAULT_BATCH_SIZE)

    if not model_name:
        raise ValueError("bulk_update requires 'model'")
    if not values:
        raise ValueError("bulk_update requires 'values'")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    model = env[model_name]
    records = model.search(domain)
    total = len(records)
    processed = 0
    errors = []

    for i in range(0, total, batch_size):
        batch = records[i: i + batch_size]
        try:
            # Savepoint: batch gagal tidak boleh meninggalkan tulisan separuh,
            # karena _update_progress meng-commit sesudahnya.
            with env.cr.savepoint():
                batch.write(values)
        except Exception as exc:
            _logger.warning("bulk_update batch error (ids %s): %s", batch.ids, exc)
            errors.append({"ids": batch.ids, "error": str(exc)})
        processed += len(batch)
        _update_progress(job, processed, total)

    return {
        "model": model_name,
        "total": total,
        "updated": processed - sum(len(e["ids"]) for e in errors),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Bulk create
# ---------------------------------------------------------------------------

def run_bulk_create(env, args: dict, job) -> dict:
    """args: {model, records: [...]}"""
    model_name = args.get("model")
    records_data = args.get("records") or []

    if not model_name:
        raise ValueError("bulk_create requires 'model'")
    if not records_data:
        raise ValueError("bulk_create requires 'records' list")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    total = len(records_data)
    created_ids = []
    errors = []

    model = env[model_name]
    batch_size = _DEFAULT_BATCH_SIZE

    for i in range(0, total, batch_size):
        batch = records_data[i: i + batch_size]
        try:
            with env.cr.savepoint():
                created = model.create(batch)
            created_ids.extend(created.ids)
        except Exception as exc:
            _logger.warning("bulk_create batch error (offset %d): %s", i, exc)
            errors.append({"offset": i, "count": len(batch), "error": str(exc)})
        _update_progress(job, min(i + batch_size, total), total)

    return {
        "model": model_name,
        "total_requested": total,
        "created": len(created_ids),
        "created_ids": created_ids[:500],  # cap to avoid oversized result
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Bulk unlink
# ---------------------------------------------------------------------------

def run_bulk_unlink(env, args: dict, job) -> dict:
    """args: {model, domain}"""
    model_name = args.get("model")
    domain = args.get("domain") or []

    if not model_name:
        raise ValueError("bulk_unlink requires 'model'")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    model = env[model_name]
    records = model.search(domain)
    total = len(records)
    deleted = 0
    errors = []
    batch_size = _DEFAULT_BATCH_SIZE

    for i in range(0, total, batch_size):
        batch = records[i: i + batch_size]
        try:
            with env.cr.savepoint():
                batch.unlink()
            deleted += len(batch)
        except Exception as exc:
            _logger.warning("bulk_unlink batch error (ids %s): %s", batch.ids, exc)
            errors.append({"ids": batch.ids, "error": str(exc)})
        _update_progress(job, i + len(batch), total)

    return {
        "model": model_name,
        "total": total,
        "deleted": deleted,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def run_export_csv(env, args: dict, job) -> dict:
    """args: {model, domain, fields}
    Creates an ir.attachment and sets job.attachment_id.
    Returns {attachment_id, row_count}.
    """
    model_name = args.get("model")
    domain = args.get("domain") or []
    field_names = args.get("fields") or []

    if not model_name:
        raise ValueError("export_csv requires 'model'")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    model = env[model_name]

    if not field_names:
        all_fields = model.fields_get(attributes=["type"])
        field_names = [
            f for f, meta in all_fields.items()
            if meta["type"] not in ("binary", "one2many", "many2many")
        ][:80]

    records = model.search_read(domain, field_names)
    total = len(records)
    _update_progress(job, 0, total)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_names, extrasaction="ignore")
    writer.writeheader()
    for i, row in enumerate(records):
        # Serialize non-string values
        clean = {}
        for k, v in row.items():
            if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
                clean[k] = v[1]  # Many2one — take display name
            elif isinstance(v, list):
                clean[k] = ";".join(str(x) for x in v)
            elif v is None or v is False:
                clean[k] = ""
            else:
                clean[k] = v
        writer.writerow(clean)
        if (i + 1) % _DEFAULT_BATCH_SIZE == 0:
            _update_progress(job, i + 1, total)

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    import base64
    ts = fields.Datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{model_name.replace('.', '_')}_{ts}.csv"

    attachment = env["ir.attachment"].sudo().create({
        "name": filename,
        "datas": base64.b64encode(csv_bytes).decode(),
        "mimetype": "text/csv",
        "res_model": "mcp.job",
        "res_id": job.id,
    })

    job.sudo().write({"attachment_id": attachment.id})

    return {
        "model": model_name,
        "row_count": total,
        "attachment_id": attachment.id,
        "filename": filename,
        "download_url": f"/web/content/{attachment.id}?download=1",
    }


# ---------------------------------------------------------------------------
# Export XLSX
# ---------------------------------------------------------------------------

def run_export_xlsx(env, args: dict, job) -> dict:
    """args: {model, domain, fields}
    Uses xlsxwriter if available; falls back to CSV otherwise.
    """
    try:
        import xlsxwriter  # noqa: F401 — check availability only
        return _export_xlsx_native(env, args, job)
    except ImportError:
        _logger.info("xlsxwriter not available — falling back to CSV export")
        return run_export_csv(env, args, job)


def _export_xlsx_native(env, args: dict, job) -> dict:
    import xlsxwriter
    import base64

    model_name = args.get("model")
    domain = args.get("domain") or []
    field_names = args.get("fields") or []

    if not model_name:
        raise ValueError("export_xlsx requires 'model'")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    model = env[model_name]

    if not field_names:
        all_fields = model.fields_get(attributes=["type"])
        field_names = [
            f for f, meta in all_fields.items()
            if meta["type"] not in ("binary", "one2many", "many2many")
        ][:80]

    records = model.search_read(domain, field_names)
    total = len(records)
    _update_progress(job, 0, total)

    buf = io.BytesIO()
    workbook = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = workbook.add_worksheet("Export")
    bold = workbook.add_format({"bold": True})

    for col, fname in enumerate(field_names):
        ws.write(0, col, fname, bold)

    for row_idx, record in enumerate(records):
        for col, fname in enumerate(field_names):
            val = record.get(fname)
            if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], int):
                val = val[1]
            elif isinstance(val, list):
                val = ";".join(str(x) for x in val)
            elif val is None or val is False:
                val = ""
            ws.write(row_idx + 1, col, val)
        if (row_idx + 1) % _DEFAULT_BATCH_SIZE == 0:
            _update_progress(job, row_idx + 1, total)

    workbook.close()
    xlsx_bytes = buf.getvalue()

    ts = fields.Datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{model_name.replace('.', '_')}_{ts}.xlsx"

    attachment = env["ir.attachment"].sudo().create({
        "name": filename,
        "datas": base64.b64encode(xlsx_bytes).decode(),
        "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "res_model": "mcp.job",
        "res_id": job.id,
    })

    job.sudo().write({"attachment_id": attachment.id})

    return {
        "model": model_name,
        "row_count": total,
        "attachment_id": attachment.id,
        "filename": filename,
        "download_url": f"/web/content/{attachment.id}?download=1",
    }


# ---------------------------------------------------------------------------
# Custom / call_method
# ---------------------------------------------------------------------------

def run_custom(env, args: dict, job) -> dict:
    """args: {model, method, ids, args, kwargs}
    Mirrors the logic of tool_executor._call_method but runs async.
    """
    model_name = args.get("model")
    method_name = args.get("method")
    ids = args.get("ids") or []
    method_args = args.get("args") or []
    method_kwargs = args.get("kwargs") or {}

    if not model_name:
        raise ValueError("call_method requires 'model'")
    if not method_name:
        raise ValueError("call_method requires 'method'")
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry")

    # Honour the same denylist as the synchronous tool
    _DENYLIST = frozenset({
        "sudo", "with_user", "with_company", "with_context", "with_env",
        "execute", "execute_kw",
        "search", "search_read", "search_count", "read", "create", "write", "unlink",
        "read_group", "_read_group", "name_search", "name_get", "default_get",
        "browse", "exists", "ensure_one", "mapped", "filtered", "sorted",
        "invalidate_recordset",
    })
    full_access = env.context.get("mcp_scope") == "admin"
    if method_name.startswith("_") and not full_access:
        raise ValueError(f"Method {method_name!r} is private and cannot be called via MCP.")
    if method_name in _DENYLIST and not full_access:
        raise ValueError(f"Method {method_name!r} is blocked.")

    model = env[model_name]
    if not hasattr(model, method_name):
        raise ValueError(f"Method {method_name!r} not found on model {model_name!r}")

    target = model.browse(ids) if ids else model
    result = getattr(target, method_name)(*method_args, **method_kwargs)

    _update_progress(job, 1, 1)

    return {"result": result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result)}
