"""The eight MCP tools: definitions shown in tools/list and their implementations."""
import decimal
import json

from . import sql
from .grants import DEFAULT_LIMIT, RESPONSE_BYTES, ToolArgumentError, check_ids

WRITE_TOOLS = ('create_record', 'update_records', 'delete_records', 'sql_insert', 'sql_update', 'sql_delete')

_MODEL = {'type': 'string', 'description': 'Technical model name, for example res.partner.'}
_FIELDS = {'type': 'array', 'items': {'type': 'string'},
           'description': 'Field names to return. Default: stored, non-binary fields this key may read.'}
_DOMAIN = {'type': 'array',
           'description': "Odoo domain: terms [field, operator, value] combined with '&', '|', '!'. "
                          "Operators: = != < <= > >= in, not in, like, ilike, not like, not ilike, =like, =ilike, child_of. "
                          "Dotted paths such as partner_id.name are allowed when every model on the path is readable."}
_IDS = {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1}
_TABLE = {'type': 'string', 'description': 'Table name in the public schema, for example res_partner.'}
_WHERE = {'type': 'array',
          'description': "Conditions [column, operator, value] combined with '&', '|', '!' (implicit AND). "
                         "Operators: = != < <= > >= in, not in, like, ilike, not like, not ilike (wrapped in %), "
                         "=like, =ilike (as given). Values are bound parameters; = null means IS NULL."}
_DRY_RUN = {'type': 'boolean', 'default': False,
            'description': 'Run the statement, report the rows it would change, and undo it.'}
_SQL_NOTE = (' SQL bypasses Odoo access rights, record rules, constraints, computed fields and tracking; '
             'prefer the record tools when they can do the job.')

DEFINITIONS = (
    {
        'name': 'list_models',
        'title': 'List models',
        'action': 'read',
        'description': 'List the Odoo models this key may use, with the actions allowed on each '
                       '(both the key permission and the Odoo access rights of the user behind the key).',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Filter on technical or display name.'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50},
        }, 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'get_model_fields',
        'title': 'Describe model fields',
        'action': 'read',
        'description': 'Describe the fields of a model that this key may read or write: type, label, '
                       'required, readonly, relation and selection values.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Only these fields.'},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'search_records',
        'title': 'Search records',
        'action': 'read',
        'description': 'Search records of a model with a domain and return their fields. '
                       'When has_more is true, call again with a larger offset.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'domain': _DOMAIN,
            'fields': _FIELDS,
            'limit': {'type': 'integer', 'minimum': 1, 'default': DEFAULT_LIMIT},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
            'order': {'type': 'string', 'description': "For example 'date desc, id desc'."},
            'include_archived': {'type': 'boolean', 'default': False},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'count_records',
        'title': 'Count records',
        'action': 'read',
        'description': 'Count the records of a model that match a domain.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'domain': _DOMAIN,
            'include_archived': {'type': 'boolean', 'default': False},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'read_records',
        'title': 'Read records',
        'action': 'read',
        'description': 'Read records of a model by id. Ids that do not exist or are not visible are listed in missing_ids.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'ids': _IDS,
            'fields': _FIELDS,
        }, 'required': ['model', 'ids'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'create_record',
        'title': 'Create a record',
        'action': 'create',
        'description': 'Create one record. many2one takes an id, many2many a list of ids, '
                       'one2many a list of new line objects (for example order lines).',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'values': {'type': 'object', 'description': 'Field values of the new record.'},
        }, 'required': ['model', 'values'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    },
    {
        'name': 'update_records',
        'title': 'Update records',
        'action': 'update',
        'description': 'Write the same values on records by id. many2many replaces the whole set; '
                       'one2many adds new lines.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'ids': _IDS,
            'values': {'type': 'object', 'description': 'Field values to write.'},
        }, 'required': ['model', 'ids', 'values'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    },
    {
        'name': 'delete_records',
        'title': 'Delete records',
        'action': 'delete',
        'description': 'Delete records by id. This cannot be undone; the deleted ids and names are kept in the audit log.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'ids': _IDS,
        }, 'required': ['model', 'ids'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_list_tables',
        'title': 'List SQL tables',
        'action': 'sql:select',
        'description': 'List the database tables this key may use with SQL, the statements allowed on each, '
                       'their primary key and readable columns.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Filter on the table name.'},
        }, 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_select',
        'title': 'Select rows',
        'action': 'sql:select',
        'description': 'Select rows from one table with structured conditions.' + _SQL_NOTE,
        'inputSchema': {'type': 'object', 'properties': {
            'table': _TABLE,
            'columns': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Default: every readable column.'},
            'where': _WHERE,
            'order': {'type': 'string', 'description': "For example 'date desc, id desc'."},
            'limit': {'type': 'integer', 'minimum': 1, 'default': 20},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
        }, 'required': ['table'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_query',
        'title': 'Run a SELECT query',
        'action': 'sql:select',
        'description': 'Run one read-only SELECT (joins, GROUP BY, WITH allowed) over tables this key may select. '
                       'Name the columns; whole rows, other tables and most functions are refused.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'One SELECT or WITH ... SELECT statement.'},
            'limit': {'type': 'integer', 'minimum': 1, 'default': 20},
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_insert',
        'title': 'Insert rows',
        'action': 'sql:insert',
        'description': 'Insert up to 20 rows into one table; create_uid, write_uid, create_date and write_date '
                       'are filled when the table has them.' + _SQL_NOTE,
        'inputSchema': {'type': 'object', 'properties': {
            'table': _TABLE,
            'rows': {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1, 'maxItems': sql.HARD_MAX_ROWS},
        }, 'required': ['table', 'rows'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    },
    {
        'name': 'sql_update',
        'title': 'Update rows',
        'action': 'sql:update',
        'description': 'Update the rows of one table that match where (required); at most 20 rows, '
                       'otherwise nothing changes.' + _SQL_NOTE,
        'inputSchema': {'type': 'object', 'properties': {
            'table': _TABLE,
            'values': {'type': 'object', 'description': 'Column values to set.'},
            'where': _WHERE,
            'dry_run': _DRY_RUN,
        }, 'required': ['table', 'values', 'where'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_delete',
        'title': 'Delete rows',
        'action': 'sql:delete',
        'description': 'Delete the rows of one table that match where (required); at most 20 rows, '
                       'otherwise nothing changes. This cannot be undone.' + _SQL_NOTE,
        'inputSchema': {'type': 'object', 'properties': {
            'table': _TABLE,
            'where': _WHERE,
            'dry_run': _DRY_RUN,
        }, 'required': ['table', 'where'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    },
)
SQL_METHODS = {'sql_list_tables': 'list_tables', 'sql_select': 'select', 'sql_query': 'query',
               'sql_insert': 'insert', 'sql_update': 'update', 'sql_delete': 'delete'}

BY_NAME = dict((tool['name'], tool) for tool in DEFINITIONS)


def visible(env, grant):
    """Tool definitions this key could use, in fixed order."""
    shown = []
    for tool in DEFINITIONS:
        action = tool['action']
        if action.startswith('sql:'):
            usable = grant.sql_any(env.cr, action[4:])
        else:
            usable = grant.any_permitted(env, action)
        if usable:
            shown.append(dict((key, value) for key, value in tool.items() if key != 'action'))
    return shown


class Outcome(object):
    """What a tool did, for the answer and the audit row."""

    def __init__(self, payload, model=None, ids=None, count=0, written=None, statement=None, dry_run=False):
        self.payload = payload
        self.model = model
        self.ids = ids or []
        self.count = count
        self.written = sorted(written or [])
        self.statement = statement
        self.dry_run = dry_run


def call(env, grant, name, arguments):
    tool = BY_NAME.get(name)
    if tool is None:
        raise ToolArgumentError("There is no tool named %s." % (name,))
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object.")
    allowed = set(tool['inputSchema']['properties'])
    extra = sorted(set(arguments) - allowed)
    if extra:
        raise ToolArgumentError("Unexpected arguments for %s: %s" % (name, ', '.join(extra)))
    missing = [key for key in tool['inputSchema'].get('required', ()) if key not in arguments]
    if missing:
        raise ToolArgumentError("Missing arguments for %s: %s" % (name, ', '.join(missing)))
    if name in SQL_METHODS and not grant.sql_any(env.cr, tool['action'][4:]):
        raise ToolArgumentError("There is no tool named %s." % (name,))
    if name in SQL_METHODS:
        run = sql.Run(env, grant)
        try:
            payload, table, keys, count = getattr(run, SQL_METHODS[name])(arguments)
        except Exception as failure:
            try:
                failure.statement = run.statement
            except (AttributeError, TypeError):
                pass
            raise
        return Outcome(payload, model=table, ids=keys, count=count, written=run.written,
                       statement=run.statement, dry_run=bool(payload.get('dry_run')))
    outcome = globals()['_' + name](env, grant, arguments)
    env.flush_all()
    return outcome


def _integer(arguments, name, default, lowest, highest):
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < lowest:
        raise ToolArgumentError("%s must be an integer of at least %s." % (name, lowest))
    if highest is not None and value > highest:
        raise ToolArgumentError("%s may be at most %s with this key." % (name, highest))
    return value


def _flag(arguments, name):
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise ToolArgumentError("%s must be true or false." % name)
    return value


def _list_models(env, grant, arguments):
    query = arguments.get('query') or ''
    if not isinstance(query, str):
        raise ToolArgumentError("query must be a string.")
    limit = _integer(arguments, 'limit', 50, 1, 100)
    access = env['ir.model.access']
    found = []
    labels = dict((rec.model, rec.name) for rec in env['ir.model'].sudo().search([('model', 'in', list(grant.rows))]))
    for name in grant.models(env):
        label = labels.get(name) or env[name]._description or name
        if query and query.lower() not in name.lower() and query.lower() not in label.lower():
            continue
        can = {}
        for action in ('read', 'create', 'update', 'delete'):
            mode = {'read': 'read', 'create': 'create', 'update': 'write', 'delete': 'unlink'}[action]
            can[action] = bool(grant.permitted(env, name, action) and access.check(name, mode, raise_exception=False))
        found.append({'model': name, 'name': label, 'can': can})
    return Outcome({'models': found[:limit], 'returned': len(found[:limit]), 'has_more': len(found) > limit},
                   count=len(found[:limit]))


def _get_model_fields(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'read')
    names = list(grant.fields(model))
    asked = arguments.get('fields')
    if asked is not None:
        names = grant.read_fields(model, asked)
    attributes = ['type', 'string', 'required', 'readonly', 'relation', 'selection', 'help']
    described = model.fields_get(allfields=names, attributes=attributes)
    fields = {}
    for name in sorted(described):
        meta = described[name]
        entry = dict((key, meta[key]) for key in attributes if meta.get(key) not in (None, False, '') or key in ('required', 'readonly'))
        if entry.get('relation') and not grant.permitted(env, entry['relation'], 'read'):
            entry['relation_readable'] = False
        fields[name] = entry
    return Outcome({'model': model._name, 'fields': fields}, model=model._name, count=len(fields))


def _search_records(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'read')
    include_archived = _flag(arguments, 'include_archived')
    model = model.with_context(active_test=not include_archived)
    domain = grant.check_domain(env, model, arguments.get('domain'))
    fields = grant.read_fields(model, arguments.get('fields'))
    limit = _integer(arguments, 'limit', DEFAULT_LIMIT, 1, grant.max_read())
    offset = _integer(arguments, 'offset', 0, 0, None)
    order = grant.check_order(model, arguments.get('order'))
    found = model.search(domain, offset=offset, limit=limit + 1, order=order)
    has_more = len(found) > limit
    records = found[:limit].read(fields)
    records, trimmed = _fit(records)
    payload = {'records': records, 'returned': len(records), 'has_more': has_more or trimmed,
               'next_offset': offset + len(records) if (has_more or trimmed) else None}
    return Outcome(payload, model=model._name, ids=[rec['id'] for rec in records], count=len(records))


def _count_records(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'read')
    model = model.with_context(active_test=not _flag(arguments, 'include_archived'))
    domain = grant.check_domain(env, model, arguments.get('domain'))
    count = model.search_count(domain)
    return Outcome({'count': count}, model=model._name, count=count)


def _read_records(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'read')
    ids = check_ids(arguments.get('ids'), grant.max_read())
    fields = grant.read_fields(model, arguments.get('fields'))
    existing = model.browse(ids).exists()
    found = dict((rec['id'], rec) for rec in existing.read(fields))
    records = [found[item] for item in ids if item in found]
    records, trimmed = _fit(records)
    payload = {'records': records, 'missing_ids': [item for item in ids if item not in found]}
    if trimmed:
        payload['not_returned_ids'] = [item for item in ids if item in found][len(records):]
    return Outcome(payload, model=model._name, ids=[rec['id'] for rec in records], count=len(records))


def _create_record(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'create')
    written = set()
    values = grant.check_values(env, model, arguments.get('values'), written)
    record = model.create(values)
    return Outcome({'id': record.id, 'display_name': record.display_name},
                   model=model._name, ids=[record.id], count=1, written=written)


def _update_records(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'update')
    ids = check_ids(arguments.get('ids'), grant.max_write())
    written = set()
    values = grant.check_values(env, model, arguments.get('values'), written)
    records = model.browse(ids).exists()
    missing = [item for item in ids if item not in records.ids]
    if missing:
        raise ToolArgumentError("Records not found on %s: %s" % (model._name, ', '.join(str(item) for item in missing)))
    records.write(values)
    return Outcome({'updated': len(records)}, model=model._name, ids=records.ids, count=len(records), written=written)


def _delete_records(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'delete')
    ids = check_ids(arguments.get('ids'), grant.max_delete())
    records = model.browse(ids).exists()
    missing = [item for item in ids if item not in records.ids]
    if missing:
        raise ToolArgumentError("Records not found on %s: %s" % (model._name, ', '.join(str(item) for item in missing)))
    deleted = [{'id': rec.id, 'display_name': rec.display_name} for rec in records]
    records.unlink()
    return Outcome({'deleted': deleted}, model=model._name, ids=[item['id'] for item in deleted], count=len(deleted))


def dumps(payload):
    return json.dumps(payload, ensure_ascii=False, default=_plain, sort_keys=True)


def _plain(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return '%s' % (value,)


def _fit(records):
    """Keep the leading records whose JSON fits in the response budget."""
    budget = RESPONSE_BYTES
    kept = []
    for record in records:
        size = len(dumps(record).encode('utf-8')) + 2
        if size > budget:
            return kept, True
        budget -= size
        kept.append(record)
    return kept, False
