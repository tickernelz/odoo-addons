# -*- coding: utf-8 -*-
"""The SQL path: structured statements on allowed tables and checked read-only SELECT queries."""
import fnmatch
import json
import re

from .grants import FORBIDDEN_COLUMN_PATTERNS, ToolArgumentError, ToolRefused, flag, integer, split_list

SCHEMA = 'public'
HARD_MAX_ROWS = 20
FORBIDDEN_TABLES = ('ir_*', 'res_users*', 'res_groups*', 'base_*', 'bus_*', 'kaitech_mcp_*',
                    'res_company_users_rel', 'rule_group_rel', 'res_device*', 'auth_totp*')
OPERATORS = {
    '=': '=', '!=': '!=', '<': '<', '<=': '<=', '>': '>', '>=': '>=',
    'like': 'LIKE', 'ilike': 'ILIKE', 'not like': 'NOT LIKE', 'not ilike': 'NOT ILIKE',
    '=like': 'LIKE', '=ilike': 'ILIKE', 'in': 'IN', 'not in': 'NOT IN',
}
WRAPPED = ('like', 'ilike', 'not like', 'not ilike')
AUDIT_COLUMNS = ('create_uid', 'write_uid', 'create_date', 'write_date')
ALLOWED_FUNCTIONS = frozenset("""
    count sum avg min max stddev variance bool_and bool_or every string_agg array_agg
    lower upper length char_length octet_length substring substr left right strpos position
    trim btrim ltrim rtrim replace split_part concat concat_ws lpad rpad initcap reverse
    coalesce nullif greatest least
    abs ceil ceiling floor round trunc sign mod power sqrt
    date_trunc date_part extract age now timezone to_char to_date to_timestamp to_number make_date
    generate_series unnest array_length cardinality
    row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value last_value nth_value
    int2 int4 int8 float4 float8 numeric text varchar bpchar date time timestamp timestamptz interval bool
""".split())
FUNCTION_CALL = re.compile(r'(?:([A-Za-z_][A-Za-z0-9_]*)\.)?"?([A-Za-z_][A-Za-z0-9_$]*)"?\(')
QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", re.S)
STRING_PREFIXES = 'EeBbXx'
IDENTIFIER_CHARS = re.compile(r'[A-Za-z0-9_$]')
UNSAFE_IN_TEXT = (';', '--', '/*', '$', '\\')
EXPRESSION_KEYS = ('Output', 'Filter', 'Join Filter', 'Hash Cond', 'Merge Cond', 'Index Cond', 'Recheck Cond',
                   'One-Time Filter', 'Sort Key', 'Group Key', 'Function Call', 'TID Cond', 'Order By',
                   'Presorted Key', 'Cache Key', 'Grouping Sets', 'Hash Key', 'Tid Cond', 'Run Condition')
REFUSED_NODES = ('ModifyTable', 'LockRows', 'Foreign Scan', 'Custom Scan', 'Sample Scan')
FUNCTION_SCANS = ('generate_series', 'unnest')
READ_COLUMNS = """
    WITH RECURSIVE rules(oid) AS (
        SELECT oid FROM pg_rewrite WHERE ev_class = 'pg_temp.kaitech_mcp_probe'::regclass
        UNION
        SELECT r.oid FROM rules
        JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = rules.oid
            AND d.refclassid = 'pg_class'::regclass
        JOIN pg_rewrite r ON r.ev_class = d.refobjid AND r.ev_type = '1'
    )
    SELECT DISTINCT c.relname, a.attname, format_type(coalesce(nullif(t.typbasetype, 0), a.atttypid), NULL) FROM rules
    JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = rules.oid
        AND d.refclassid = 'pg_class'::regclass AND d.refobjsubid > 0
    JOIN pg_class c ON c.oid = d.refobjid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = %s
    JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
    JOIN pg_type t ON t.oid = a.atttypid
"""


def strip_quoted(text):
    """Text of one SQL fragment with literals and quoted names removed; never raises, so checks stay conservative."""
    parts = []
    position = 0
    for found in QUOTED.finditer(text):
        parts.append(text[position:found.start()])
        position = found.end()
    parts.append(text[position:])
    return ' '.join(parts)


def lex(text):
    """Split one SQL text into (whole text, bare text) and refuse anything whose literals cannot be placed."""
    parts = []
    position = 0
    for found in QUOTED.finditer(text):
        parts.append(text[position:found.start()])
        position = found.end()
    parts.append(text[position:])
    bare = []
    for index, part in enumerate(parts):
        if '"' in part or "'" in part:
            raise ToolArgumentError("The query has an unterminated quote.")
        if index < len(parts) - 1 and part:
            last = part[-1]
            before = part[-2:-1]
            if last in STRING_PREFIXES and not IDENTIFIER_CHARS.match(before):
                raise ToolArgumentError("Strings with a %s prefix are not allowed here." % last)
        for marker in UNSAFE_IN_TEXT:
            if marker in part:
                raise ToolArgumentError("The query may not contain %s outside a string." % marker)
        bare.append(part)
    return text, ' '.join(bare)


def single_statement(text):
    """Return (text, bare) for one statement, where bare is the text without literals or quoted names."""
    text = text.strip()
    if text.endswith(';'):
        text = text[:-1].rstrip()
    statements, bare = lex(text)
    if bare.count('(') != bare.count(')'):
        raise ToolArgumentError("The query has unbalanced parentheses.")
    if not re.match(r'(?is)^\(*\s*(select|with)\b', bare.strip()):
        raise ToolArgumentError("Only SELECT queries are allowed here; use sql_insert, sql_update or sql_delete to change data.")
    return statements, bare


def quote(name):
    return '"%s"' % name.replace('"', '""')


def table_is_forbidden(table, settings):
    patterns = FORBIDDEN_TABLES + tuple(split_list(settings.get('extra_forbidden_tables')))
    return any(fnmatch.fnmatchcase(table, pattern) for pattern in patterns)


def database_tables(cr):
    """Names of base tables and views in the public schema."""
    cr.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name", (SCHEMA,))
    return [row[0] for row in cr.fetchall()]


def column_is_hidden(column, kind, forbidden):
    return column in forbidden or kind == 'bytea' or any(
        fnmatch.fnmatchcase(column, pattern) for pattern in FORBIDDEN_COLUMN_PATTERNS)


def load_tables(cr, names, grant):
    """Describe the given public tables in two queries; return {name: Table} for those that exist."""
    names = sorted(set(names))
    if not names:
        return {}
    cr.execute("""
        SELECT c.table_name, c.column_name, c.data_type, c.is_nullable = 'YES', c.column_default IS NOT NULL,
               t.table_type
          FROM information_schema.columns c
          JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE c.table_schema = %s AND c.table_name = ANY(%s) ORDER BY c.table_name, c.ordinal_position""",
               (SCHEMA, names))
    columns = {}
    for row in cr.fetchall():
        columns.setdefault(row[0], []).append(row[1:])
    cr.execute("""
        SELECT c.relname, a.attname FROM pg_index i
          JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
         WHERE i.indisprimary AND n.nspname = %s AND c.relname = ANY(%s) ORDER BY c.relname, a.attnum""",
               (SCHEMA, names))
    keys = {}
    for table, column in cr.fetchall():
        keys.setdefault(table, []).append(column)
    return dict((name, Table(name, rows, keys.get(name, []), grant)) for name, rows in columns.items())


def load_table(cr, name, grant):
    table = load_tables(cr, [name], grant).get(name)
    if table is None:
        raise ToolArgumentError("Unknown table: %s" % name)
    return table


class Table(object):
    """Columns, types and primary key of one public table, with the columns this key may use."""

    def __init__(self, name, rows, primary_key, grant):
        self.name = name
        self.is_view = rows[0][4] != 'BASE TABLE'
        forbidden = grant.columns_forbidden_on(name)
        self.columns = []
        self.types = {}
        self.defaults = set()
        for column, kind, nullable, has_default, _kind in rows:
            if column_is_hidden(column, kind, forbidden):
                continue
            self.columns.append(column)
            self.types[column] = (kind, nullable)
            if has_default:
                self.defaults.add(column)
        self.hidden = set(row[0] for row in rows) - set(self.columns)
        self.primary_key = list(primary_key)

    def column(self, name):
        if not isinstance(name, basestring) or name not in self.types:
            raise ToolArgumentError("Unknown or forbidden column on %s: %s" % (self.name, name))
        return quote(name)

    def describe(self):
        return [{'name': name, 'type': self.types[name][0], 'nullable': self.types[name][1]} for name in self.columns]

    def returning(self):
        if not self.primary_key or any(name in self.hidden for name in self.primary_key):
            return '', False
        return ' RETURNING %s' % ', '.join(quote(name) for name in self.primary_key), True


def _scalar(value):
    return value is None or isinstance(value, (bool, int, long, float, basestring))


def where_clause(table, where, required=False):
    """Translate a Polish-notation condition list into SQL text and bound parameters."""
    if where is None:
        where = []
    if not isinstance(where, list):
        raise ToolArgumentError("where must be a list of [column, operator, value] terms and '&', '|', '!'.")
    if required and not [term for term in where if isinstance(term, list)]:
        raise ToolArgumentError("where is required and must contain at least one condition.")
    stack = []
    for term in reversed(where):
        if term == '!':
            if not stack:
                raise ToolArgumentError("'!' needs one condition after it.")
            text, params = stack.pop()
            stack.append(('(NOT %s)' % text, params))
        elif term in ('&', '|'):
            if len(stack) < 2:
                raise ToolArgumentError("'%s' needs two conditions after it." % term)
            left, right = stack.pop(), stack.pop()
            joiner = ' AND ' if term == '&' else ' OR '
            stack.append(('(%s%s%s)' % (left[0], joiner, right[0]), left[1] + right[1]))
        elif isinstance(term, list) and len(term) == 3:
            stack.append(_condition(table, term))
        else:
            raise ToolArgumentError("Each where term is '&', '|', '!' or [column, operator, value].")
    if not stack:
        return 'TRUE', []
    stack.reverse()
    return ' AND '.join(text for text, _bound in stack), [value for _text, bound in stack for value in bound]


def _condition(table, term):
    column, operator, value = term
    target = table.column(column)
    if operator not in OPERATORS:
        raise ToolArgumentError("Operator %r is not allowed." % (operator,))
    if operator in ('in', 'not in'):
        if not isinstance(value, list) or not all(_scalar(item) and item is not None for item in value):
            raise ToolArgumentError("%s needs a list of values." % operator)
        if not value:
            return ('FALSE' if operator == 'in' else 'TRUE'), []
        return '%s %s %%s' % (target, OPERATORS[operator]), [tuple(value)]
    if not _scalar(value):
        raise ToolArgumentError("Values must be numbers, strings, booleans or null.")
    if value is None:
        if operator not in ('=', '!='):
            raise ToolArgumentError("null can only be compared with = or !=.")
        return '%s IS %sNULL' % (target, '' if operator == '=' else 'NOT '), []
    if operator in WRAPPED:
        value = '%%%s%%' % value
    return '%s %s %%s' % (target, OPERATORS[operator]), [value]


def order_clause(table, order):
    if order is None or order == '':
        return ''
    if not isinstance(order, basestring):
        raise ToolArgumentError("order must be a string such as 'name asc, id desc'.")
    parts = []
    for chunk in order.split(','):
        words = chunk.split()
        if not words or len(words) > 2 or (len(words) == 2 and words[1].lower() not in ('asc', 'desc')):
            raise ToolArgumentError("Cannot order by %r." % chunk.strip())
        parts.append('%s %s' % (table.column(words[0]), words[1].upper() if len(words) == 2 else 'ASC'))
    return ' ORDER BY ' + ', '.join(parts)


def _values(table, values, written):
    if not isinstance(values, dict) or not values:
        raise ToolArgumentError("values must be a non-empty object of column: value.")
    names = sorted(values)
    for name in names:
        table.column(name)
        if name in AUDIT_COLUMNS:
            raise ToolArgumentError("%s is filled by the server; leave it out." % name)
        if not _scalar(values[name]):
            raise ToolArgumentError("Column %s takes a number, string, boolean or null." % name)
        written.add('%s.%s' % (table.name, name))
    return names


def _within_limit(cr, table, condition, params, most, what):
    """Refuse before changing anything when the condition matches more than most rows."""
    cr.execute('SELECT count(*) FROM (SELECT 1 FROM %s WHERE %s LIMIT %%s) matched' % (quote(table.name), condition),
               params + [most + 1])
    if cr.fetchone()[0] > most:
        raise ToolArgumentError("The where matches more than %s rows; at most %s can be %s in one call. Nothing was changed."
                                % (most, most, what))


def _changed(cr, table, statement, params, most, dry_run, what):
    """Run one INSERT/UPDATE/DELETE in a savepoint; undo it when over the row limit or on dry_run."""
    cr.execute('SAVEPOINT kaitech_mcp_sql')
    try:
        cr.execute(statement, params)
        keys = [list(row) if len(row) > 1 else row[0] for row in cr.fetchall()] if cr.description else []
        count = cr.rowcount
    except Exception:
        cr.execute('ROLLBACK TO SAVEPOINT kaitech_mcp_sql')
        raise
    if count > most:
        cr.execute('ROLLBACK TO SAVEPOINT kaitech_mcp_sql')
        raise ToolArgumentError("The where matches more than %s rows; at most %s can be %s in one call. Nothing was changed."
                                % (most, most, what))
    if dry_run:
        cr.execute('ROLLBACK TO SAVEPOINT kaitech_mcp_sql')
    else:
        cr.execute('RELEASE SAVEPOINT kaitech_mcp_sql')
    return count, keys


class Run(object):
    """One SQL tool call: the statement and keys it touched, for the answer and the audit."""

    def __init__(self, env, grant):
        self.env = env
        self.cr = env.cr
        self.grant = grant
        self.statement = None
        self.written = set()

    def table(self, name, action):
        self.grant.require_table(self.cr, name, action)
        table = load_table(self.cr, name, self.grant)
        if table.is_view and action != 'select':
            raise ToolRefused("%s is a view; only select is possible." % name)
        return table

    def list_tables(self, arguments):
        query = arguments.get('query') or ''
        if not isinstance(query, basestring):
            raise ToolArgumentError("query must be a string.")
        found = []
        names = [name for name in self.grant.sql_table_names(self.cr) if not query or query.lower() in name.lower()]
        tables = load_tables(self.cr, names, self.grant)
        for name in names:
            table = tables.get(name)
            if table is None:
                continue
            can = dict((action, self.grant.table_permitted(self.cr, name, action) and (action == 'select' or not table.is_view))
                       for action in ('select', 'insert', 'update', 'delete'))
            found.append({'table': name, 'can': can, 'primary_key': table.primary_key, 'columns': table.describe()})
        return {'tables': found}, None, [], len(found)

    def select(self, arguments):
        table = self.table(arguments.get('table'), 'select')
        columns = arguments.get('columns')
        if columns is None:
            columns = table.columns
        if not isinstance(columns, list) or not columns:
            raise ToolArgumentError("columns must be a non-empty list of column names.")
        selected = ', '.join(table.column(name) for name in columns)
        condition, params = where_clause(table, arguments.get('where'))
        limit = integer(arguments, 'limit', 20, 1, self.grant.max_read())
        offset = integer(arguments, 'offset', 0, 0, None)
        self.statement = 'SELECT %s FROM %s WHERE %s%s LIMIT %%s OFFSET %%s' % (
            selected, quote(table.name), condition, order_clause(table, arguments.get('order')))
        self.cr.execute(self.statement, params + [limit + 1, offset])
        rows = [dict(zip(columns, row)) for row in self.cr.fetchall()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        keys = [row.get(table.primary_key[0]) for row in rows] if len(table.primary_key) == 1 else []
        payload = {'rows': rows, 'returned': len(rows), 'has_more': has_more,
                   'next_offset': offset + len(rows) if has_more else None}
        return payload, table.name, keys, len(rows)

    def query(self, arguments):
        if not self.grant.sql_any(self.cr, 'select'):
            raise ToolRefused("This key may not run SQL queries.")
        text = arguments.get('query')
        if not isinstance(text, basestring) or not text.strip():
            raise ToolArgumentError("query must be one SELECT statement.")
        limit = integer(arguments, 'limit', 20, 1, self.grant.max_read())
        text, _bare = single_statement(text)
        self.statement = text
        cr = self.cr
        cr.execute('SAVEPOINT kaitech_mcp_query')
        try:
            cr.execute('SET LOCAL standard_conforming_strings = on')
            cr.execute('SAVEPOINT kaitech_mcp_check')
            self.check_columns(text)
            cr.execute('ROLLBACK TO SAVEPOINT kaitech_mcp_check')
            cr.execute('RELEASE SAVEPOINT kaitech_mcp_check')
            cr.execute('SET LOCAL transaction_read_only = on')
            cr.execute('SET LOCAL statement_timeout = %s' % int(self.grant.settings['sql_statement_timeout_ms']))
            cr.execute('EXPLAIN (VERBOSE, FORMAT JSON) ' + text)
            plan = cr.fetchone()[0]
            if isinstance(plan, basestring):
                plan = json.loads(plan)
            tables = self.check_plan(plan)
            cr.execute(text)
            columns = [column[0] for column in cr.description]
            if len(set(columns)) != len(columns):
                raise ToolArgumentError("Give every selected column a distinct name.")
            fetched = cr.fetchmany(limit + 1)
        finally:
            cr.execute('ROLLBACK TO SAVEPOINT kaitech_mcp_query')
            cr.execute('RELEASE SAVEPOINT kaitech_mcp_query')
        rows = [dict(zip(columns, row)) for row in fetched[:limit]]
        payload = {'columns': columns, 'rows': rows, 'returned': len(rows), 'has_more': len(fetched) > limit}
        return payload, ','.join(sorted(tables)), [], len(rows)

    def check_columns(self, text):
        """Refuse a query that reads a hidden column anywhere in it, whatever name the query gives the column."""
        self.cr.execute('CREATE TEMP VIEW kaitech_mcp_probe AS SELECT 1 FROM (%s) AS kaitech_mcp_probe' % text)
        self.cr.execute(READ_COLUMNS, (SCHEMA,))
        for table, column, kind in sorted(self.cr.fetchall()):
            if column_is_hidden(column, kind, self.grant.columns_forbidden_on(table)):
                raise ToolRefused("Column %s is never readable through MCP." % column)

    def check_plan(self, plan):
        """Refuse a plan that reads a table, a whole row or a function this key may not use."""
        tables = set()
        expressions = []
        nodes = [item['Plan'] for item in plan if isinstance(item, dict) and 'Plan' in item]
        while nodes:
            node = nodes.pop()
            nodes.extend(child for child in node.get('Plans') or [] if isinstance(child, dict))
            kind = node.get('Node Type')
            if kind in REFUSED_NODES:
                raise ToolRefused("This query is not allowed (%s)." % kind)
            if kind == 'Function Scan' and node.get('Function Name') not in FUNCTION_SCANS:
                raise ToolRefused("Function %s is not allowed in queries." % node.get('Function Name'))
            if 'Relation Name' in node:
                name, schema = node['Relation Name'], node.get('Schema')
                if schema != SCHEMA or not self.grant.table_permitted(self.cr, name, 'select'):
                    raise ToolRefused("This key may not read table %s." % (name if schema == SCHEMA else '%s.%s' % (schema, name)))
                tables.add(name)
            for key in EXPRESSION_KEYS:
                value = node.get(key)
                if isinstance(value, basestring):
                    expressions.append(value)
                elif isinstance(value, list):
                    expressions.extend(item for item in value if isinstance(item, basestring))
        for expression in expressions:
            plain = strip_quoted(expression)
            if '.*' in plain:
                raise ToolRefused("Name the columns instead of selecting whole rows.")
            for schema, function in FUNCTION_CALL.findall(plain):
                if (schema and schema != 'pg_catalog') or function.lower() not in ALLOWED_FUNCTIONS:
                    raise ToolRefused("Function %s is not allowed in queries." % function)
        return tables

    def insert(self, arguments):
        table = self.table(arguments.get('table'), 'insert')
        rows = arguments.get('rows')
        if not isinstance(rows, list) or not rows or len(rows) > HARD_MAX_ROWS:
            raise ToolArgumentError("rows must be a list of 1 to %s objects." % HARD_MAX_ROWS)
        returning, has_key = table.returning()
        keys = []
        for row in rows:
            names = _values(table, row, self.written)
            for name in names:
                if name in table.primary_key and name in table.defaults:
                    raise ToolArgumentError("%s is filled by the database; leave it out." % name)
            values = dict((name, row[name]) for name in names)
            for name in AUDIT_COLUMNS:
                if name in table.types and name not in values:
                    values[name] = self.env.uid if name.endswith('_uid') else _NOW
            ordered = sorted(values)
            placeholders = ', '.join("(now() AT TIME ZONE 'UTC')" if values[name] is _NOW else '%s' for name in ordered)
            self.statement = 'INSERT INTO %s (%s) VALUES (%s)%s' % (
                quote(table.name), ', '.join(quote(name) for name in ordered), placeholders, returning)
            _count, inserted = _changed(self.cr, table, self.statement,
                                        [values[name] for name in ordered if values[name] is not _NOW],
                                        1, False, 'inserted')
            keys.extend(inserted)
        self.env.registry.clear_caches()
        return {'inserted': len(rows), 'keys': keys}, table.name, keys if has_key else [], len(rows)

    def update(self, arguments):
        table = self.table(arguments.get('table'), 'update')
        dry_run = flag(arguments, 'dry_run')
        values = arguments.get('values')
        names = _values(table, values, self.written)
        for name in names:
            if name in table.primary_key:
                raise ToolArgumentError("Primary key column %s cannot be updated." % name)
        condition, params = where_clause(table, arguments.get('where'), required=True)
        assignments = ['%s = %%s' % quote(name) for name in names]
        bound = [values[name] for name in names]
        if 'write_uid' in table.types and 'write_uid' not in names:
            assignments.append('"write_uid" = %s')
            bound.append(self.env.uid)
        if 'write_date' in table.types and 'write_date' not in names:
            assignments.append("\"write_date\" = (now() AT TIME ZONE 'UTC')")
        returning, has_key = table.returning()
        self.statement = 'UPDATE %s SET %s WHERE %s%s' % (quote(table.name), ', '.join(assignments), condition, returning)
        most = min(HARD_MAX_ROWS, self.grant.max_write())
        _within_limit(self.cr, table, condition, params, most, 'updated')
        count, keys = _changed(self.cr, table, self.statement, bound + params, most, dry_run, 'updated')
        if not dry_run:
            self.env.registry.clear_caches()
        return {'updated': count, 'keys': keys, 'dry_run': dry_run}, table.name, keys if has_key else [], count

    def delete(self, arguments):
        table = self.table(arguments.get('table'), 'delete')
        dry_run = flag(arguments, 'dry_run')
        condition, params = where_clause(table, arguments.get('where'), required=True)
        returning, has_key = table.returning()
        self.statement = 'DELETE FROM %s WHERE %s%s' % (quote(table.name), condition, returning)
        _within_limit(self.cr, table, condition, params, self.grant.max_delete(), 'deleted')
        count, keys = _changed(self.cr, table, self.statement, params, self.grant.max_delete(), dry_run, 'deleted')
        if not dry_run:
            self.env.registry.clear_caches()
        return {'deleted': count, 'keys': keys, 'dry_run': dry_run}, table.name, keys if has_key else [], count


class _Now(object):
    pass


_NOW = _Now()
