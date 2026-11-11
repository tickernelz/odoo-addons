"""SQL tools: structured statements and checked SELECT queries on the tables a token may use."""
from . import sql
from .grants import Grant, ToolArgumentError, ToolRefused


def check_arguments(definition, arguments):
    """Refuse arguments the tool schema does not list, and missing required ones."""
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object.")
    schema = definition['inputSchema']
    extra = sorted(set(arguments) - set(schema['properties']))
    if extra:
        raise ToolArgumentError("Unexpected arguments for %s: %s" % (definition['name'], ', '.join(extra)))
    missing = [key for key in schema.get('required', ()) if key not in arguments]
    if missing:
        raise ToolArgumentError("Missing arguments for %s: %s" % (definition['name'], ', '.join(missing)))


_TABLE = {'type': 'string', 'description': 'Table name in the public schema, for example res_partner.'}
_WHERE = {'type': 'array',
          'description': "Conditions [column, operator, value] combined with '&', '|', '!' (implicit AND). "
                         "Operators: = != < <= > >= in, not in, like, ilike, not like, not ilike (wrapped in %), "
                         "=like, =ilike (as given). Values are bound parameters; = null means IS NULL."}
_DRY_RUN = {'type': 'boolean', 'default': False,
            'description': 'Run the statement, report the rows it would change, and undo it.'}
_SQL_NOTE = (' SQL bypasses Odoo access rights, record rules, constraints, computed fields and tracking; '
             'prefer the record tools when they can do the job.')

TOOL_DEFINITIONS = [
    {
        'name': 'sql_list_tables',
        'title': 'List SQL tables',
        'description': 'List the database tables this token may use with SQL, the statements allowed on each, '
                       'their primary key and readable columns.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Filter on the table name.'},
        }, 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'sql_select',
        'title': 'Select rows',
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
        'description': 'Run one read-only SELECT (joins, GROUP BY, WITH allowed) over tables this token may select. '
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
        'description': 'Delete the rows of one table that match where (required); at most 20 rows, '
                       'otherwise nothing changes. This cannot be undone.' + _SQL_NOTE,
        'inputSchema': {'type': 'object', 'properties': {
            'table': _TABLE,
            'where': _WHERE,
            'dry_run': _DRY_RUN,
        }, 'required': ['table', 'where'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    },
]
_BY_NAME = dict((tool['name'], tool) for tool in TOOL_DEFINITIONS)
ACTIONS = {
    'sql_list_tables': 'select', 'sql_select': 'select', 'sql_query': 'select',
    'sql_insert': 'insert', 'sql_update': 'update', 'sql_delete': 'delete',
}
_RUNS = {
    'sql_list_tables': 'list_tables', 'sql_select': 'select', 'sql_query': 'query',
    'sql_insert': 'insert', 'sql_update': 'update', 'sql_delete': 'delete',
}
READ_ONLY_TOOL_NAMES = frozenset(name for name, action in ACTIONS.items() if action == 'select')


def visible(env, grant):
    """Names of the SQL tools this token can use."""
    if grant is None or not grant.sql_open:
        return set()
    return set(name for name, action in ACTIONS.items() if grant.sql_any(env.cr, action))


def _handler(name):
    def run_tool(env, args):
        grant = Grant.of(env)
        if grant is None or not grant.sql_any(env.cr, ACTIONS[name]):
            raise ToolRefused("This token may not use %s. An administrator enables SQL in the MCP settings, "
                              "on the token, and per table." % name)
        check_arguments(_BY_NAME[name], args)
        cr = env.cr
        cr.execute("SHOW statement_timeout")
        previous = cr.fetchone()[0]
        cr.execute("SET LOCAL statement_timeout = %s", (max(100, grant.settings['statement_timeout_ms']),))
        run = sql.Run(env, grant)
        payload, _table, _keys, _count = getattr(run, _RUNS[name])(args)
        cr.execute("SET LOCAL statement_timeout = %s", (previous,))
        if run.statement:
            payload['statement'] = run.statement
        return payload
    return run_tool


TOOL_HANDLERS = dict((name, _handler(name)) for name in ACTIONS)
