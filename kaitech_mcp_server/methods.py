# -*- coding: utf-8 -*-
"""call_method: run one public business method as the key's user, optionally rolled back."""
import json
from contextlib import contextmanager

from odoo import models

from . import explore
from .grants import RESPONSE_BYTES, ToolArgumentError, ToolRefused, check_ids, flag

SAVEPOINT = 'kaitech_mcp_call_method'
CHANGES_RECORDS = 20
MAX_RESULT_IDS = 200

DEFINITION = {
    'name': 'call_method',
    'title': 'Call a method',
    'action': 'call',
    'description': "Call a public business method (no leading _, not an ORM method such as write or unlink) on "
                   "records of a model this key may call, as the key's user. dry_run runs it and rolls the database "
                   "back; anything outside the database (a mail sent at once, a file, an outside call) is not undone. "
                   "changes lists the stored fields that changed on those records.",
    'inputSchema': {'type': 'object', 'properties': {
        'model': {'type': 'string', 'description': 'Technical model name, for example sale.order.'},
        'method': {'type': 'string', 'description': 'Method name, for example action_confirm.'},
        'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1,
                'description': 'Records to call it on. Omit for model-level methods.'},
        'args': {'type': 'array', 'description': 'Positional arguments after self.'},
        'kwargs': {'type': 'object', 'description': 'Keyword arguments.'},
        'context': {'type': 'object', 'description': 'Extra context keys, for example {"lang": "id_ID"}.'},
        'dry_run': {'type': 'boolean', 'default': False,
                    'description': 'Run it, report the result and changes, then undo every database change.'},
    }, 'required': ['model', 'method'], 'additionalProperties': False},
    'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': True},
}


def callable_method(env, model, method):
    """Refuse private, framework and unknown methods; return the method name."""
    if not isinstance(method, basestring) or not method:
        raise ToolArgumentError("method must be a method name.")
    if method.startswith('_'):
        raise ToolRefused("Private methods (starting with _) cannot be called.")
    if explore.is_framework_method(method):
        raise ToolRefused("%s is an ORM method; use the record tools instead." % method)
    if not explore.index(env.registry).methods(model._name).get(method):
        raise ToolArgumentError("%s has no method %s. search_methods finds methods by part of their name."
                                % (model._name, method))
    return method


def plain(value):
    if isinstance(value, models.BaseModel):
        return {'model': value._name, 'ids': value.ids[:MAX_RESULT_IDS]}
    if isinstance(value, dict):
        return dict(('%s' % (key,), plain(item)) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (basestring, bool, int, long, float)):
        return value
    return '%s' % (value,)


def _snapshot(records, fields):
    records.invalidate_cache()
    return dict((row['id'], row) for row in records.exists().read(fields))


def _changes(before, after):
    changes = {}
    for record_id, old in before.items():
        new = after.get(record_id)
        if new is None:
            changes['%s' % record_id] = 'deleted'
            continue
        changed = dict((name, [old[name], new[name]]) for name in old if name != 'id' and old[name] != new[name])
        if changed:
            changes['%s' % record_id] = changed
    return changes


def _run(records, method, args, kwargs, watched, fields):
    result = plain(getattr(records, method)(*args, **kwargs))
    records.recompute()
    return result, (_snapshot(watched, fields) if watched else {})


@contextmanager
def _rolled_back(env, method):
    """Run the block in a savepoint that is always rolled back; a commit inside it is refused."""
    cr = env.cr
    saved = dict((name, cr.__dict__.get(name)) for name in ('commit', 'rollback'))

    def refuse():
        raise ToolRefused("%s commits or rolls back the transaction itself, so it cannot run with dry_run." % method)

    cr.execute('SAVEPOINT %s' % SAVEPOINT)
    cr.commit = cr.rollback = refuse
    try:
        yield
    finally:
        for name, original in saved.items():
            if original is None:
                del cr.__dict__[name]
            else:
                setattr(cr, name, original)
        cr.execute('ROLLBACK TO SAVEPOINT %s' % SAVEPOINT)
        env.clear()


def call(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'call')
    method = callable_method(env, model, arguments.get('method'))
    args = arguments.get('args', [])
    kwargs = arguments.get('kwargs', {})
    context = arguments.get('context', {})
    if not isinstance(args, list):
        raise ToolArgumentError("args must be a list.")
    if not isinstance(kwargs, dict):
        raise ToolArgumentError("kwargs must be an object.")
    if not isinstance(context, dict):
        raise ToolArgumentError("context must be an object.")
    if context:
        merged = dict(model.env.context)
        merged.update(context)
        model = model.with_context(merged)
    dry_run = flag(arguments, 'dry_run')
    records = model.browse()
    if arguments.get('ids') is not None:
        ids = check_ids(arguments['ids'], grant.max_write())
        visible = model.with_context(active_test=False).search([('id', 'in', ids)]).ids
        missing = [item for item in ids if item not in visible]
        if missing:
            raise ToolArgumentError("Records not found on %s: %s" % (model._name, ', '.join('%s' % item for item in missing)))
        records = model.browse(ids)
    watched = records[:CHANGES_RECORDS] if grant.permitted(env, model._name, 'read') else model.browse()
    fields = grant.default_read_fields(model) if watched else []
    before = _snapshot(watched, fields) if watched else {}
    if dry_run:
        with _rolled_back(env, method):
            result, after = _run(records, method, args, kwargs, watched, fields)
    else:
        result, after = _run(records, method, args, kwargs, watched, fields)
    changes = _changes(before, after)
    payload = {'model': model._name, 'method': method, 'ids': records.ids, 'result': result, 'dry_run': dry_run}
    if len(json.dumps(result)) > RESPONSE_BYTES // 2:
        payload['result'] = {'truncated': True, 'type': type(result).__name__}
    if changes:
        payload['changes'] = changes
    written = set('%s.%s' % (model._name, name) for change in changes.values()
                  if isinstance(change, dict) for name in change)
    return payload, {'model': model._name, 'ids': records.ids, 'count': len(records), 'written': written,
                     'dry_run': dry_run, 'method': method}
