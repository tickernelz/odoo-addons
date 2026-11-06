"""The MCP tools: definitions shown in tools/list and their implementations."""
import datetime
import decimal
import json

from odoo.models import BaseModel

from . import explore, sql
from .grants import DEFAULT_LIMIT, RESPONSE_BYTES, ToolArgumentError, ToolRefused, check_ids

WRITE_TOOLS = ('create_record', 'update_records', 'delete_records', 'sql_insert', 'sql_update', 'sql_delete',
               'call_method')
_READ_ONLY = {'readOnlyHint': True, 'openWorldHint': False}

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
_METHOD = {'type': 'string', 'description': 'Method name, for example action_confirm or _compute_amounts.'}
_PAGE = {'type': 'integer', 'minimum': 0, 'default': 0}
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
            'all': {'type': 'boolean', 'default': False,
                    'description': "Every model of the database, not only this key's (needs Explore code)."},
            'field': {'type': 'string', 'description': 'With all: only models that have this field, with its type.'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50},
            'offset': _PAGE,
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
EXPLORE_DEFINITIONS = (
    {
        'name': 'describe_model',
        'title': 'Describe a model',
        'action': 'explore',
        'description': 'One call to understand a model: each field as one line (type, label, compute method with '
                       'its depends, related, selection keys, defining modules), defining and extending modules, '
                       'parent models, public methods, and what this key and its user may do.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Only these fields.'},
            'query': {'type': 'string', 'description': 'Keep fields and methods whose name contains this.'},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_methods',
        'title': 'Search methods',
        'action': 'explore',
        'description': 'Find methods by name, across all models or in one model (inherited ones included). '
                       'Private methods such as _compute_amount are included.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Words the method name contains, for example confirm.'},
            'model': _MODEL,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
            'offset': _PAGE,
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'describe_method',
        'title': 'Describe a method',
        'action': 'explore',
        'description': 'Signature, decorators, docstring, the fields it computes, and every override in MRO order '
                       '(module, file:line, calls super) of one method.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'method': _METHOD,
        }, 'required': ['model', 'method'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'get_method_source',
        'title': 'Method source',
        'action': 'explore',
        'description': 'Source code of a method and of every override, most derived first, with file and lines.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'method': _METHOD,
            'module': {'type': 'string', 'description': 'Only the definition in this module.'},
            'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 300},
        }, 'required': ['model', 'method'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_code',
        'title': 'Search code',
        'action': 'explore',
        'description': 'Search the code of the installed modules (or one module). Returns module/path:line: text.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Text to find; a regular expression when regex is true.'},
            'module': {'type': 'string'},
            'file_types': {'type': 'array', 'items': {'type': 'string', 'enum': list(explore.SOURCE_TYPES)},
                           'description': 'Default: py, xml, js, csv.'},
            'regex': {'type': 'boolean', 'default': False},
            'ignore_case': {'type': 'boolean', 'default': False},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'read_source',
        'title': 'Read a code file',
        'action': 'explore',
        'description': 'Read numbered lines of one code file of a module (py, xml, js, csv, po, pot).',
        'inputSchema': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'module/relative/path, for example sale/models/sale_order.py.'},
            'start_line': {'type': 'integer', 'minimum': 1, 'default': 1},
            'lines': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 200},
        }, 'required': ['path'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'list_modules',
        'title': 'List modules',
        'action': 'explore',
        'description': 'Modules of this database with title, version, state and folder.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Filter on name or title.'},
            'state': {'type': 'string', 'enum': ['installed', 'uninstalled', 'to upgrade', 'to install', 'all'],
                      'default': 'installed'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
            'offset': _PAGE,
        }, 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'describe_module',
        'title': 'Describe a module',
        'action': 'explore',
        'description': 'Manifest, dependencies, models a module defines or extends, its wizards, and the views, '
                       'menus, actions, crons, rules and groups it ships.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': {'type': 'string', 'description': 'Technical module name, for example sale.'},
        }, 'required': ['module'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'get_view',
        'title': 'Get a view',
        'action': 'explore',
        'description': "A model's view as this key's user sees it after every inheritance, and the views it is "
                       'built from.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'view_type': {'type': 'string', 'default': 'form', 'description': 'form, list, kanban, search, ...'},
            'view': {'description': 'A view xmlid or id instead of the default view.'},
            'start_line': {'type': 'integer', 'minimum': 1, 'default': 1},
            'lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 300},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'list_actions',
        'title': 'List actions',
        'action': 'explore',
        'description': 'Window, server and report actions of a model or matching a name, with the menus that open them.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'query': {'type': 'string', 'description': 'Filter on the action name.'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30},
        }, 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_xmlids',
        'title': 'Search external ids',
        'action': 'explore',
        'description': 'Find external ids (module.name) and the record each points to.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Part of the id, or module.name_part.'},
            'model': _MODEL,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'call_method',
        'title': 'Call a method',
        'action': 'call',
        'description': 'Call a public business method (for example action_confirm) as the key user; private and '
                       'ORM methods (write, unlink, ...) are refused. Returns the result and the fields that changed '
                       'on the records. dry_run runs it and rolls the database back; email or HTTP calls the method '
                       'makes are not undone.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'method': _METHOD,
            'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1},
                    'description': 'Records to call it on; omit for @api.model methods.'},
            'args': {'type': 'array', 'description': 'Positional arguments after the records.'},
            'kwargs': {'type': 'object'},
            'context': {'type': 'object', 'description': 'Extra context keys.'},
            'dry_run': {'type': 'boolean', 'default': False},
        }, 'required': ['model', 'method'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': False},
    },
)
DEFINITIONS = DEFINITIONS[:2] + EXPLORE_DEFINITIONS[:-1] + DEFINITIONS[2:] + EXPLORE_DEFINITIONS[-1:]

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
        elif action == 'explore':
            usable = grant.explore
        else:
            usable = grant.any_permitted(env, action)
        if usable:
            shown.append(dict((key, value) for key, value in tool.items() if key != 'action'))
    return shown


class Outcome(object):
    """What a tool did, for the answer and the audit row."""

    def __init__(self, payload, model=None, ids=None, count=0, written=None, statement=None, dry_run=False,
                 undo=False):
        self.payload = payload
        self.undo = undo
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
    if (tool['action'] == 'explore' and not grant.explore) or (tool['action'] == 'call' and not grant.any_permitted(env, 'call')):
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
    offset = _integer(arguments, 'offset', 0, 0, None)
    field = _text(arguments, 'field')
    if _flag(arguments, 'all'):
        if not grant.explore:
            raise ToolRefused("Listing every model needs a key with Explore code.")
        return _all_models(env, query, limit, offset, field)
    if field:
        raise ToolArgumentError("field works only with all: true.")
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
        if grant.call_open:
            can['call'] = grant.permitted(env, name, 'call')
        found.append({'model': name, 'name': label, 'can': can})
    page = found[offset:offset + limit]
    return Outcome({'models': page, 'returned': len(page), 'has_more': len(found) > offset + limit},
                   count=len(page))


def _all_models(env, query, limit, offset, field=None):
    needle = query.lower()
    rows = []
    for name in sorted(env.registry):
        model = env[name]
        label = model._description or name
        if needle and needle not in name.lower() and needle not in label.lower():
            continue
        row = [name, label, type(model)._original_module, explore.kind_of(model)]
        if field:
            found = model._fields.get(field)
            if found is None:
                continue
            comodel = getattr(found, 'comodel_name', None)
            row.append('%s(%s)' % (found.type, comodel) if comodel else found.type)
        rows.append(row)
    page = rows[offset:offset + limit]
    more = len(rows) > offset + limit
    columns = ['model', 'description', 'module', 'kind'] + (['field'] if field else [])
    return Outcome({'columns': columns, 'models': page, 'returned': len(page),
                    'total': len(rows), 'has_more': more, 'next_offset': offset + limit if more else None},
                   count=len(page))


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


def _text(arguments, name, required=False):
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError("%s must be a non-empty string." % name)
    return value.strip()


def _names(arguments, name):
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("%s must be a list of names." % name)
    return value


def _explored(payload, model=None, count=0):
    return Outcome(payload, model=model, count=count)


def _describe_model(env, grant, arguments):
    model = _text(arguments, 'model', True)
    payload = explore.describe_model(env, grant, model, _names(arguments, 'fields'), _text(arguments, 'query'))
    return _explored(payload, model, sum(len(group) for group in payload['fields'].values()))


def _search_methods(env, grant, arguments):
    payload = explore.search_methods(env, _text(arguments, 'query', True), _text(arguments, 'model'),
                                     _integer(arguments, 'limit', 50, 1, 200), _integer(arguments, 'offset', 0, 0, None))
    return _explored(payload, arguments.get('model'), payload['returned'])


def _describe_method(env, grant, arguments):
    model = _text(arguments, 'model', True)
    return _explored(explore.describe_method(env, model, _text(arguments, 'method', True)), model, 1)


def _get_method_source(env, grant, arguments):
    model = _text(arguments, 'model', True)
    payload = explore.method_source(env, model, _text(arguments, 'method', True), _text(arguments, 'module'),
                                    _integer(arguments, 'max_lines', 300, 1, 1000))
    return _explored(payload, model, len(payload['definitions']))


def _search_code(env, grant, arguments):
    payload = explore.search_code(env, arguments.get('query'), _text(arguments, 'module'), _names(arguments, 'file_types'),
                                  _flag(arguments, 'regex'), _flag(arguments, 'ignore_case'),
                                  _integer(arguments, 'limit', 50, 1, 200))
    return _explored(payload, count=payload['returned'])


def _read_source(env, grant, arguments):
    payload = explore.read_source(env, arguments.get('path'), _integer(arguments, 'start_line', 1, 1, None),
                                  _integer(arguments, 'lines', 200, 1, 500))
    return _explored(payload, count=payload['end_line'] - payload['start_line'] + 1)


def _list_modules(env, grant, arguments):
    state = arguments.get('state', 'installed')
    if state not in ('installed', 'uninstalled', 'to upgrade', 'to install', 'all'):
        raise ToolArgumentError("state must be installed, uninstalled, to upgrade, to install or all.")
    payload = explore.list_modules(env, _text(arguments, 'query'), state, _integer(arguments, 'limit', 50, 1, 200),
                                   _integer(arguments, 'offset', 0, 0, None))
    return _explored(payload, count=payload['returned'])


def _describe_module(env, grant, arguments):
    return _explored(explore.describe_module(env, _text(arguments, 'module', True)), count=1)


def _get_view(env, grant, arguments):
    model = _text(arguments, 'model', True)
    view_type = _text(arguments, 'view_type') or 'form'
    payload = explore.get_view(env, model, view_type, arguments.get('view'),
                               _integer(arguments, 'start_line', 1, 1, None), _integer(arguments, 'lines', 300, 1, 1000))
    return _explored(payload, model, 1)


def _list_actions(env, grant, arguments):
    model = _text(arguments, 'model')
    payload = explore.list_actions(env, model, _text(arguments, 'query'), _integer(arguments, 'limit', 30, 1, 100))
    return _explored(payload, model, payload['returned'])


def _search_xmlids(env, grant, arguments):
    model = _text(arguments, 'model')
    payload = explore.search_xmlids(env, _text(arguments, 'query', True), model, _integer(arguments, 'limit', 50, 1, 200))
    return _explored(payload, model, payload['returned'])


def _refuse_commit():
    raise ToolRefused("This method commits by itself, so dry_run cannot undo it; nothing was changed.")


def _call_method(env, grant, arguments):
    model = grant.require(env, arguments.get('model'), 'call')
    method = _text(arguments, 'method', True)
    allowed, reason = explore.callable_by_tool(env, model._name, method)
    if not allowed:
        raise ToolRefused("%s.%s cannot be called through MCP: %s." % (model._name, method, reason))
    is_model_method = getattr(getattr(type(model), method), '_api_model', False)
    ids = arguments.get('ids')
    if ids is not None:
        if is_model_method:
            raise ToolArgumentError("%s.%s works on the model, not on records; call it without ids." % (model._name, method))
        ids = check_ids(ids, grant.max_write())
    args = arguments.get('args') or []
    kwargs = arguments.get('kwargs') or {}
    context = arguments.get('context') or {}
    if not isinstance(args, list):
        raise ToolArgumentError("args must be a list.")
    if not isinstance(kwargs, dict) or 'context' in kwargs:
        raise ToolArgumentError("kwargs must be an object; pass context separately.")
    if not isinstance(context, dict):
        raise ToolArgumentError("context must be an object.")
    dry_run = _flag(arguments, 'dry_run')
    records = model.browse(ids or [])
    if ids:
        missing = [item for item in ids if item not in records.exists().ids]
        if missing:
            raise ToolArgumentError("Records not found on %s: %s" % (model._name, ', '.join(str(item) for item in missing)))
    if context:
        records = records.with_context(**context)
    watched = [name for name in grant.default_read_fields(model) if model._fields[name].type != 'many2many'] if ids else []
    before = _snapshot(records, watched)
    cursor = env.cr
    if dry_run:
        cursor.commit = _refuse_commit
    try:
        result = getattr(records, method)(*args, **kwargs)
        env.flush_all()
    finally:
        if dry_run:
            del cursor.commit
    records.invalidate_recordset()
    after = _snapshot(records.exists(), watched)
    changes = {}
    written = set()
    for record_id, old in before.items():
        new = after.get(record_id)
        if new is None:
            changes[record_id] = 'deleted'
            continue
        diff = dict((name, [old[name], new[name]]) for name in watched if old.get(name) != new.get(name))
        if diff:
            changes[record_id] = diff
            written.update('%s.%s' % (model._name, name) for name in diff)
    payload = {'result': _jsonable(result), 'changes': changes, 'dry_run': dry_run}
    statement = dumps({'method': method, 'ids': ids, 'args': args, 'kwargs': kwargs, 'context': context})
    return Outcome(payload, model=model._name, ids=ids or [], count=len(ids or []), written=written,
                   statement='%s.%s %s' % (model._name, method, statement), dry_run=dry_run, undo=dry_run)


def _snapshot(records, fields):
    if not records or not fields:
        return {}
    return dict((row['id'], row) for row in records.read(fields))


def _jsonable(value, depth=0):
    if depth > 6:
        return '%s' % (value,)
    if isinstance(value, BaseModel):
        shown = {'model': value._name, 'ids': value.ids[:100]}
        if len(value) <= 20:
            shown['names'] = [record.display_name for record in value]
        return shown
    if isinstance(value, dict):
        return dict(('%s' % (key,), _jsonable(item, depth + 1)) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item, depth + 1) for item in value]
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return _plain(value)


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
