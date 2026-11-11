"""
MCP tool definitions and handlers for the async job queue.

This file is intentionally self-contained. It will be merged into the main
tool registry by the consolidation agent. Do NOT modify tool_executor.py or
mcp_tool_registry.py — append/patch from here.

Scope enforcement:
  - read scope  : odoo_job_status and odoo_job_list only
  - write scope : all four tools
  - admin scope : all four tools
"""
import json
import logging

from .json_utils import odoo_json_default

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema definitions — advertised via tools/list
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "odoo_submit_job",
        "description": (
            "Submit a heavy operation to the MCP async job queue and get a job_id immediately. "
            "Use for bulk updates, bulk creates, bulk deletes, and data exports that would "
            "otherwise exceed HTTP timeout limits. "
            "Poll status with odoo_job_status(job_id=...) until state is 'done' or 'failed'. "
            "Operations: bulk_update, bulk_create, bulk_unlink, export_csv, export_xlsx, "
            "call_method, custom."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "bulk_update",
                        "bulk_create",
                        "bulk_unlink",
                        "export_csv",
                        "export_xlsx",
                        "call_method",
                        "custom",
                    ],
                    "description": (
                        "Operation type. "
                        "bulk_update: args={model, domain, values, batch_size?}. "
                        "bulk_create: args={model, records:[...]}. "
                        "bulk_unlink: args={model, domain}. "
                        "export_csv/xlsx: args={model, domain?, fields?}. "
                        "call_method/custom: args={model, method, ids?, args?, kwargs?}."
                    ),
                },
                "args": {
                    "type": "object",
                    "description": "Operation-specific arguments object. See operation description.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                    "description": "Job priority hint (informational — cron processes FIFO by default).",
                },
            },
            "required": ["operation", "args"],
        },
    },
    {
        "name": "odoo_job_status",
        "description": (
            "Get the current status of an async MCP job. "
            "Returns state (pending/running/done/failed/cancelled), progress (0-100), "
            "processed_records, total_records, error_message (if failed), "
            "and result summary (if done). "
            "For export jobs, result includes a download_url for the generated file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID returned by odoo_submit_job.",
                },
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "odoo_job_list",
        "description": (
            "List recent async MCP jobs. By default returns only your own jobs. "
            "Admin users can pass my_jobs_only=false to see all users' jobs. "
            "Filter by state to find pending, running, or failed jobs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["pending", "running", "done", "failed", "cancelled"],
                    "description": "Filter by job state. Omit to return all states.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of jobs to return.",
                },
                "my_jobs_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "When false (admin only), list jobs from all users.",
                },
            },
        },
    },
    {
        "name": "odoo_job_cancel",
        "description": (
            "Cancel a pending async job. "
            "Only pending jobs can be cancelled — running, done, or failed jobs cannot be cancelled. "
            "Use odoo_job_list to find job IDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID to cancel.",
                },
            },
            "required": ["job_id"],
        },
    },
]

# ---------------------------------------------------------------------------
# Scope gate helper
# ---------------------------------------------------------------------------

_JOB_READ_TOOLS = frozenset({"odoo_job_status", "odoo_job_list"})
_JOB_WRITE_TOOLS = frozenset({"odoo_submit_job", "odoo_job_cancel"})


def _check_job_scope(env, tool_name: str) -> None:
    """Raise PermissionError if the current MCP scope does not permit *tool_name*."""
    scope = env.context.get("mcp_scope", "write")
    if scope == "admin":
        return
    if scope == "read" and tool_name in _JOB_WRITE_TOOLS:
        raise PermissionError(
            f"Tool {tool_name!r} requires at least 'write' scope. "
            f"This token is restricted to 'read' (read-only) scope."
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _submit_job(env, args: dict) -> dict:
    """Create an mcp.job record and return its id immediately."""
    _check_job_scope(env, "odoo_submit_job")

    operation = args.get("operation")
    job_args = args.get("args") or {}

    if not operation:
        raise ValueError("odoo_submit_job requires 'operation'")

    valid_ops = {
        "bulk_update", "bulk_create", "bulk_unlink",
        "export_csv", "export_xlsx",
        "call_method", "custom",
    }
    if operation not in valid_ops:
        raise ValueError(f"Unknown operation {operation!r}. Valid: {sorted(valid_ops)}")

    # Snapshot governance context into payload so async runner uses same rules
    scope = env.context.get("mcp_scope", "write")
    restrictions = env.context.get("mcp_restrictions")

    payload = {
        "args": job_args,
        "mcp_scope": scope,
        "mcp_restrictions_json": json.dumps(restrictions, default=odoo_json_default) if restrictions else None,
    }

    job_vals = {
        "operation": operation,
        "user_id": env.uid,
        "payload": json.dumps(payload, ensure_ascii=False, default=odoo_json_default),
        "mcp_scope": scope,
        "mcp_restrictions": json.dumps(restrictions, default=odoo_json_default) if restrictions else False,
    }

    # Optionally link session/token from context
    session_id = env.context.get("mcp_session_db_id")
    if session_id:
        job_vals["session_id"] = session_id
    token_id = env.context.get("mcp_token_id")
    if token_id:
        job_vals["token_id"] = token_id

    job = env["mcp.job"].sudo().create(job_vals)

    return {
        "job_id": job.id,
        "state": job.state,
        "operation": job.operation,
        "message": (
            f"Job {job.id} submitted. "
            f"Poll status with odoo_job_status(job_id={job.id}). "
            f"The cron runs every minute."
        ),
    }


def _job_status(env, args: dict) -> dict:
    """Return current status of a job."""
    _check_job_scope(env, "odoo_job_status")

    job_id = args.get("job_id")
    if not job_id:
        raise ValueError("odoo_job_status requires 'job_id'")

    job = env["mcp.job"].sudo().search([("id", "=", job_id)], limit=1)
    if not job:
        raise ValueError(f"Job {job_id} not found or not accessible.")

    result = {
        "job_id": job.id,
        "name": job.name,
        "operation": job.operation,
        "state": job.state,
        "progress": job.progress,
        "total_records": job.total_records,
        "processed_records": job.processed_records,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_ms": job.duration_ms,
        "error_message": job.error_message or None,
        "user": job.user_id.name if job.user_id else None,
    }

    if job.state == "done" and job.result:
        try:
            result["result"] = json.loads(job.result)
        except (json.JSONDecodeError, TypeError):
            result["result"] = job.result

    if job.attachment_id:
        result["attachment"] = {
            "id": job.attachment_id.id,
            "name": job.attachment_id.name,
            "download_url": f"/web/content/{job.attachment_id.id}?download=1",
        }

    return result


def _job_list(env, args: dict) -> dict:
    """List recent MCP jobs."""
    _check_job_scope(env, "odoo_job_list")

    state = args.get("state")
    limit = min(int(args.get("limit") or 20), 100)
    my_jobs_only = args.get("my_jobs_only", True)

    domain = []
    if state:
        domain.append(("state", "=", state))

    scope = env.context.get("mcp_scope", "write")
    if my_jobs_only or scope not in ("admin",):
        domain.append(("user_id", "=", env.uid))

    jobs = env["mcp.job"].sudo().search_read(
        domain,
        [
            "id", "name", "operation", "state", "progress",
            "total_records", "processed_records",
            "create_date", "started_at", "finished_at", "duration_ms",
            "user_id", "error_message",
        ],
        order="id desc",
        limit=limit,
    )

    # Serialize datetimes
    for j in jobs:
        for dt_field in ("create_date", "started_at", "finished_at"):
            if j.get(dt_field):
                j[dt_field] = j[dt_field].isoformat() if hasattr(j[dt_field], "isoformat") else str(j[dt_field])
        if isinstance(j.get("user_id"), tuple):
            j["user_id"] = {"id": j["user_id"][0], "name": j["user_id"][1]}

    return {
        "count": len(jobs),
        "jobs": jobs,
    }


def _job_cancel(env, args: dict) -> dict:
    """Cancel a pending job."""
    _check_job_scope(env, "odoo_job_cancel")

    job_id = args.get("job_id")
    if not job_id:
        raise ValueError("odoo_job_cancel requires 'job_id'")

    job = env["mcp.job"].sudo().search(
        [("id", "=", job_id), ("user_id", "=", env.uid)],
        limit=1,
    )
    if not job:
        # Admins can cancel any job
        scope = env.context.get("mcp_scope", "write")
        if scope == "admin":
            job = env["mcp.job"].sudo().search([("id", "=", job_id)], limit=1)
        if not job:
            raise ValueError(f"Job {job_id} not found or you do not own it.")

    if job.state != "pending":
        raise ValueError(
            f"Job {job_id} cannot be cancelled: state is '{job.state}' (must be 'pending')."
        )

    job.write({"state": "cancelled"})

    return {
        "job_id": job.id,
        "state": job.state,
        "message": f"Job {job.id} has been cancelled.",
    }


# ---------------------------------------------------------------------------
# Handler dispatch table — consumed by consolidation agent
# ---------------------------------------------------------------------------

TOOL_HANDLERS = {
    "odoo_submit_job": _submit_job,
    "odoo_job_status": _job_status,
    "odoo_job_list": _job_list,
    "odoo_job_cancel": _job_cancel,
}
