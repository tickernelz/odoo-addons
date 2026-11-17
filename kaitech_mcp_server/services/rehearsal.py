import copy
import json

from odoo.exceptions import UserError

from .json_utils import odoo_json_default

TOOLS = frozenset({"odoo_create", "odoo_write", "odoo_unlink", "odoo_call_method"})
NOTE = (
    "Rolled back: nothing was stored. Effects outside the database (email, chat messages, "
    "HTTP calls) are not undone."
)
PROPERTY = {
    "type": "boolean",
    "default": False,
    "description": (
        "Run the call inside a savepoint, report what it would change (rows per model, record ids, "
        "field differences) and roll everything back. Effects outside the database, such as email, "
        "chat messages or HTTP calls the code makes, cannot be rolled back."
    ),
}
_UNCOMPARED_FIELDS = frozenset({"write_date", "write_uid", "__last_update"})
_ROW_COUNTS = """
    SELECT relname, n_tup_ins, n_tup_upd, n_tup_del
      FROM pg_stat_xact_user_tables
     WHERE schemaname = current_schema()
"""


def wanted(args: dict) -> bool:
    """Whether the call asks for a dry run."""
    flag = args.get("dry_run", False)
    if not isinstance(flag, bool):
        raise ValueError("'dry_run' must be true or false.")
    return flag


def refuse_commit():
    raise PermissionError("This method commits by itself, so dry_run cannot undo it: nothing was changed.")


def rehearse(env, run, before=None, after=None):
    """Run run() in a savepoint that is always rolled back; return (its result, what it changed)."""
    cr = env.cr
    cr.flush()
    queues = _saved_queues(cr)
    caches = set(env.registry.cache_invalidated)
    counts = _row_counts(cr)
    savepoint = cr.savepoint()
    cr.commit = refuse_commit
    try:
        seen = before() if before else None
        result = run()
        cr.flush()
        changes = {"rows": _row_changes(env, counts, _row_counts(cr))}
        if after:
            changes.update(after(result, seen))
        result = _plain(result)
        changes = _plain(changes)
    finally:
        del cr.commit
        savepoint.close(rollback=True)
        _restore_queues(cr, queues)
        env.invalidate_all()
        stale = env.registry.cache_invalidated - caches
        if stale:
            env.registry.clear_cache(*stale)
    return result, changes


def rehearse_tool(env, tool_name: str, args: dict, dispatch, allowed_fields) -> dict:
    """Dry run of a record write tool: its usual answer plus the changes it would make."""
    arguments = {key: value for key, value in args.items() if key != "dry_run"}
    model_name = arguments.get("model")
    known = isinstance(model_name, str) and model_name in env.registry
    ids = _ids(arguments.get("ids")) if known else []
    before = after = None
    if known and tool_name in ("odoo_write", "odoo_call_method") and ids:
        names = _comparable_fields(env, model_name, allowed_fields)

        def before():
            return _read(env, model_name, ids, names)

        def after(_result, seen):
            return dict({"records": len(ids), "ids": ids}, **_differences(seen, _read(env, model_name, ids, names)))
    elif known and tool_name == "odoo_create":

        def after(result, _seen):
            record_id = result.get("id") if isinstance(result, dict) else None
            values = arguments.get("values") or {}
            names = [name for name in _readable_fields(env, model_name, allowed_fields) if name in values]
            read = _read(env, model_name, [record_id], names + ["display_name"]) if record_id else None
            return {"created": {"id": record_id, "values": (read or {}).get(record_id)}}
    elif known and tool_name == "odoo_unlink":

        def before():
            return _read(env, model_name, ids, ["display_name"])

        def after(_result, seen):
            return {"deleted": list((seen or {}).values())}

    result, changes = rehearse(env, lambda: dispatch(arguments), before, after)
    answer = dict(result) if isinstance(result, dict) else {"result": result}
    answer.update(dry_run=True, changes=changes, note=NOTE)
    return answer


def _ids(value) -> list:
    if isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return value
    return []


def _readable_fields(env, model_name: str, allowed_fields) -> dict:
    described = env[model_name].fields_get(attributes=["type", "store", "relation"])
    return {
        name: field for name, field in described.items()
        if (allowed_fields is None or name in allowed_fields)
        and (not field.get("relation") or field["relation"] not in env.registry or env[field["relation"]].has_access("read"))
    }


def _comparable_fields(env, model_name: str, allowed_fields) -> list:
    return [
        name for name, field in _readable_fields(env, model_name, allowed_fields).items()
        if field.get("store") and field.get("type") not in ("binary", "one2many") and name not in _UNCOMPARED_FIELDS
    ]


def _read(env, model_name: str, ids: list, names: list):
    records = env[model_name].browse(ids).exists()
    try:
        with env.cr.savepoint(flush=False):
            return {row["id"]: row for row in _plain(records.read(names))}
    except UserError:
        return None


def _differences(before, after) -> dict:
    if before is None or after is None:
        return {"differences": [], "differences_unavailable": "the records are not readable with this token"}
    differences = []
    for record_id, old in before.items():
        new = after.get(record_id)
        if new is None:
            differences.append({"id": record_id, "gone": True})
            continue
        fields = {
            name: {"before": value, "after": new.get(name)}
            for name, value in old.items() if name != "id" and value != new.get(name)
        }
        if fields:
            differences.append({"id": record_id, "fields": fields})
    return {"differences": differences}


def _row_counts(cr) -> dict:
    cr.execute(_ROW_COUNTS)
    return {table: (inserted, updated, deleted) for table, inserted, updated, deleted in cr.fetchall()}


def _row_changes(env, before: dict, after: dict) -> list:
    models_by_table = {model._table: name for name, model in env.registry.models.items() if not model._abstract}
    rows = []
    for table, counts in sorted(after.items()):
        delta = [now - was for now, was in zip(counts, before.get(table, (0, 0, 0)))]
        if any(delta):
            rows.append({
                "model": models_by_table.get(table), "table": table,
                "inserted": delta[0], "updated": delta[1], "deleted": delta[2],
            })
    return rows


def _saved_queues(cr) -> list:
    return [
        (queue, list(queue._funcs), {key: copy.copy(value) for key, value in queue.data.items()})
        for queue in (cr.postcommit, cr.prerollback, cr.postrollback)
    ]


def _restore_queues(cr, saved: list) -> None:
    for queue, funcs, data in saved:
        queue.clear()
        queue._funcs.extend(funcs)
        queue.data.update(data)


def _plain(value):
    return json.loads(json.dumps(value, default=odoo_json_default))
