import json

from . import rehearsal
from .json_utils import odoo_json_default

TOOL_NAME = "odoo_batch"
MAX_STEPS = 50
_STEP_TOOLS = {
    "create": "odoo_create",
    "write": "odoo_write",
    "unlink": "odoo_unlink",
    "call_method": "odoo_call_method",
}

TOOL_DEFINITIONS = [{
    "name": TOOL_NAME,
    "description": (
        "Run up to 50 create, write, unlink and call_method steps in ONE transaction: if any step fails, every "
        "step is rolled back and the error names the failed step. Each step takes the arguments of its tool "
        "(odoo_create, odoo_write, odoo_unlink, odoo_call_method) plus 'op', and passes the same token checks "
        "(scope, model and field limits, access rights). A value {\"ref\": \"<step>.<key>[.<key>...]\"} "
        "is replaced by part of an earlier step's result, e.g. {\"ref\": \"0.id\"} is the id step 0 created. "
        "A step whose method commits by itself is refused. dry_run applies to the whole batch: it reports what "
        "would change and rolls back. Effects outside the database (email, chat messages, HTTP calls) and work "
        "the code commits on a separate database cursor cannot be rolled back."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": sorted(_STEP_TOOLS)},
                        "model": {"type": "string"},
                        "values": {"type": "object"},
                        "ids": {"type": "array"},
                        "method": {"type": "string"},
                        "args": {"type": "array"},
                        "kwargs": {"type": "object"},
                    },
                    "required": ["op", "model"],
                },
                "description": "e.g. [{\"op\": \"create\", \"model\": \"res.partner\", \"values\": {\"name\": \"A\"}}, "
                               "{\"op\": \"write\", \"model\": \"res.partner\", \"ids\": [{\"ref\": \"0.id\"}], "
                               "\"values\": {\"city\": \"Bandung\"}}]",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Run every step, report what would change, then roll the whole batch back.",
            },
        },
        "required": ["steps"],
    },
}]


def run_batch(env, args: dict, execute_tool) -> dict:
    """Run the steps of an odoo_batch call in one transaction, each through execute_tool."""
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("'steps' must be a non-empty list.")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"Too many steps: {len(steps)} given, at most {MAX_STEPS} per batch.")
    if rehearsal.wanted(args):
        result, changes = rehearsal.rehearse(env, lambda: _run_steps(env, steps, execute_tool))
        return dict(result, dry_run=True, changes=changes, note=rehearsal.NOTE)
    cr = env.cr
    cr.commit = rehearsal.refuse_commit
    try:
        with cr.savepoint():
            return _run_steps(env, steps, execute_tool)
    finally:
        del cr.commit


def _run_steps(env, steps: list, execute_tool) -> dict:
    results = []
    for index, step in enumerate(steps):
        tool_name, arguments = _step(index, step, results)
        try:
            answer = execute_tool(env, tool_name, arguments)
        except Exception as exc:
            raise ValueError(
                f"Step {index} ({step['op']} {arguments.get('model')}) failed, so no step of the batch was kept: {exc}"
            ) from exc
        results.append(json.loads(json.dumps(answer, default=odoo_json_default)))
    return {"steps": len(results), "results": results}


def _step(index: int, step, results: list) -> tuple:
    if not isinstance(step, dict):
        raise ValueError(f"Step {index} must be an object.")
    op = step.get("op")
    if op not in _STEP_TOOLS:
        raise ValueError(f"Step {index}: 'op' must be one of {', '.join(sorted(_STEP_TOOLS))}.")
    if "dry_run" in step:
        raise ValueError(f"Step {index}: dry_run belongs to the whole batch, not to one step.")
    arguments = _resolve({key: value for key, value in step.items() if key != "op"}, results, index)
    return _STEP_TOOLS[op], arguments


def _resolve(value, results: list, index: int):
    if isinstance(value, dict):
        if set(value) == {"ref"} and isinstance(value["ref"], str):
            return _lookup(value["ref"], results, index)
        return {key: _resolve(item, results, index) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, results, index) for item in value]
    return value


def _lookup(ref: str, results: list, index: int):
    head, *path = ref.split(".")
    if not head.isdigit() or int(head) >= index:
        raise ValueError(f"Step {index}: ref {ref!r} must start with an earlier step number, e.g. '0.id'.")
    value = results[int(head)]
    for key in path:
        if isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ValueError(f"Step {index}: ref {ref!r} is not in the result of step {head}.")
    return value
