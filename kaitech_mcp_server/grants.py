# -*- coding: utf-8 -*-
"""What one MCP key may touch: settings, forbidden models and fields, argument validation."""
import fnmatch
import numbers

PARAM_PREFIX = 'kaitech_mcp_server.'

DEFAULTS = {
    'delete_enabled': False,
    'sql_enabled': False,
    'sql_statement_timeout_ms': 5000,
    'audit_retention_days': 90,
    'default_expiry_days': 90,
    'default_rate_per_minute': 60,
    'default_writes_per_minute': 20,
    'default_max_read': 200,
    'default_max_write': 100,
    'default_max_delete': 20,
    'extra_forbidden_models': '',
    'extra_forbidden_fields': '',
    'extra_forbidden_tables': '',
}

HARD_MAX_DELETE = 20
HARD_MAX_READ = 1000
RESPONSE_BYTES = 256 * 1024
DEFAULT_LIMIT = 20

FORBIDDEN_MODELS = (
    'ir.*', 'res.users', 'res.users.*', 'res.groups', 'res.groups.*',
    'base.*', 'bus.*', 'kaitech.mcp.*',
)
FORBIDDEN_FIELDS = (
    'password', 'password_crypt', 'new_password', 'signup_token', 'oauth_access_token',
)
MAGIC_FIELDS = ('id', 'create_uid', 'create_date', 'write_uid', 'write_date', '__last_update', 'display_name')
RELATIONAL = ('many2one', 'one2many', 'many2many')
DOMAIN_OPERATORS = (
    '=', '!=', '<', '<=', '>', '>=', 'in', 'not in', 'like', 'ilike', 'not like',
    'not ilike', '=like', '=ilike', 'child_of',
)
ACTIONS = ('read', 'create', 'update', 'delete')
ODOO_MODE = {'read': 'read', 'create': 'create', 'update': 'write', 'delete': 'unlink'}


class ToolArgumentError(Exception):
    """The client sent arguments this key may not use or that are malformed."""


class ToolRefused(Exception):
    """The key has no permission for what was asked."""


def split_list(text):
    if not text:
        return []
    return [item.strip() for item in text.replace('\n', ',').split(',') if item.strip()]


def read_settings(cr):
    cr.execute("SELECT key, value FROM ir_config_parameter WHERE key LIKE %s", (PARAM_PREFIX + '%',))
    stored = dict((key[len(PARAM_PREFIX):], value) for key, value in cr.fetchall())
    settings = {}
    for name, default in DEFAULTS.items():
        raw = stored.get(name)
        if raw is None or raw == '':
            settings[name] = default
        elif isinstance(default, bool):
            settings[name] = raw.strip().lower() in ('1', 'true', 'yes')
        elif isinstance(default, int):
            try:
                settings[name] = int(raw)
            except ValueError:
                settings[name] = default
        else:
            settings[name] = raw
    return settings


def forbidden_model_patterns(settings):
    return tuple(FORBIDDEN_MODELS) + tuple(split_list(settings.get('extra_forbidden_models')))


def model_is_forbidden(model_name, patterns):
    return any(fnmatch.fnmatchcase(model_name, pattern) for pattern in patterns)


def forbidden_field_rules(settings):
    """Return (names forbidden on every model, {model: names forbidden on that model})."""
    everywhere = set(FORBIDDEN_FIELDS)
    per_model = {}
    for item in split_list(settings.get('extra_forbidden_fields')):
        model, dot, name = item.rpartition('.')
        if dot and model:
            per_model.setdefault(model, set()).add(name)
        else:
            everywhere.add(item)
    return everywhere, per_model


def modelable(env, model_name):
    """True when model_name is a persistent model of this database's registry."""
    if model_name not in env.registry:
        return False
    model = env[model_name]
    return not model._transient and model._auto and not model._abstract


class Grant(object):
    """Permissions of one key for one request, read once from the database."""

    def __init__(self, key, rows, settings, tables=None):
        self.key_id = key['id']
        self.key_name = key['name']
        self.user_id = key['user_id']
        self.limits = dict((name, key[name]) for name in (
            'rate_per_minute', 'writes_per_minute', 'max_read', 'max_write', 'max_delete'))
        self.settings = settings
        self.delete_enabled = settings['delete_enabled']
        self.model_patterns = forbidden_model_patterns(settings)
        self.fields_everywhere, self.fields_per_model = forbidden_field_rules(settings)
        self.rows = rows
        self._fields_cache = {}
        self.sql_open = bool(settings['sql_enabled'] and key.get('allow_sql'))
        self.tables = tables or {}
        self._existing_tables = None

    def table_permitted(self, cr, table, action):
        from .sql import database_tables, table_is_forbidden
        row = self.tables.get(table)
        if not self.sql_open or not row or not row[action]:
            return False
        if action == 'delete' and not self.delete_enabled:
            return False
        if table_is_forbidden(table, self.settings):
            return False
        if self._existing_tables is None:
            self._existing_tables = set(database_tables(cr))
        return table in self._existing_tables

    def sql_any(self, cr, action):
        return any(self.table_permitted(cr, name, action) for name in self.tables)

    def sql_table_names(self, cr):
        return sorted(name for name in self.tables
                      if any(self.table_permitted(cr, name, action) for action in ('select', 'insert', 'update', 'delete')))

    def require_table(self, cr, table, action):
        if not isinstance(table, basestring) or not self.table_permitted(cr, table, action):
            if isinstance(table, basestring) and self.table_permitted(cr, table, 'select'):
                raise ToolRefused("This key may not %s rows of %s." % (action, table))
            raise ToolArgumentError("Unknown table: %s" % (table,))

    def columns_forbidden_on(self, table):
        forbidden = set(self.fields_everywhere)
        for model, names in self.fields_per_model.items():
            if model == table or model.replace('.', '_') == table:
                forbidden |= names
        return forbidden

    def max_read(self):
        return max(1, min(self.limits['max_read'] or DEFAULTS['default_max_read'], HARD_MAX_READ))

    def max_write(self):
        return max(1, min(self.limits['max_write'] or DEFAULTS['default_max_write'], HARD_MAX_READ))

    def max_delete(self):
        return max(1, min(self.limits['max_delete'] or DEFAULTS['default_max_delete'], HARD_MAX_DELETE))

    def permitted(self, env, model_name, action):
        row = self.rows.get(model_name)
        if not row or not row[action]:
            return False
        if action == 'delete' and not self.delete_enabled:
            return False
        if model_is_forbidden(model_name, self.model_patterns):
            return False
        return modelable(env, model_name)

    def any_permitted(self, env, action):
        return any(self.permitted(env, name, action) for name in self.rows)

    def models(self, env):
        return sorted(name for name in self.rows if any(self.permitted(env, name, action) for action in ACTIONS))

    def require(self, env, model_name, action):
        if not isinstance(model_name, basestring) or not self.permitted(env, model_name, action):
            if self.rows.get(model_name) and self.permitted(env, model_name, 'read'):
                raise ToolRefused("This key may not %s %s records." % (action, model_name))
            raise ToolArgumentError("Unknown model: %s" % (model_name,))
        return env[model_name]

    def fields(self, model):
        """Fields of model this key may see: visible to the Odoo user, not forbidden, not binary."""
        name = model._name
        if name in self._fields_cache:
            return self._fields_cache[name]
        visible = model.fields_get(attributes=['type'])
        row = self.rows.get(name)
        listed = row and row['fields']
        forbidden = self.fields_everywhere | self.fields_per_model.get(name, set())
        allowed = {}
        for fname, meta in visible.items():
            if fname in forbidden or meta.get('type') == 'binary':
                continue
            if listed and fname not in listed and fname != 'id':
                continue
            field = model._fields.get(fname)
            if field is None:
                continue
            allowed[fname] = field
        self._fields_cache[name] = allowed
        return allowed

    def default_read_fields(self, model):
        return sorted(name for name, field in self.fields(model).items()
                      if field.store and field.type != 'one2many')

    def read_fields(self, model, asked):
        allowed = self.fields(model)
        if asked is None:
            return self.default_read_fields(model)
        if not isinstance(asked, list) or not all(isinstance(name, basestring) for name in asked):
            raise ToolArgumentError("fields must be a list of field names.")
        unknown = [name for name in asked if name not in allowed]
        if unknown:
            raise ToolArgumentError("Unknown or forbidden fields on %s: %s" % (model._name, ', '.join(unknown)))
        return list(asked)

    def check_domain(self, env, model, domain):
        if domain is None:
            return []
        if not isinstance(domain, list):
            raise ToolArgumentError("domain must be a list.")
        for leaf in domain:
            if leaf in ('&', '|', '!'):
                continue
            if not isinstance(leaf, list) or len(leaf) != 3:
                raise ToolArgumentError("Each domain term is '&', '|', '!' or [field, operator, value].")
            path, operator, value = leaf
            if not isinstance(path, basestring) or not path:
                raise ToolArgumentError("A domain field must be a field name.")
            if operator not in DOMAIN_OPERATORS:
                raise ToolArgumentError("Domain operator %r is not allowed." % (operator,))
            self._check_domain_value(value, operator)
            self._check_path(env, model, path)
        return [tuple(leaf) if isinstance(leaf, list) else leaf for leaf in domain]

    def _check_domain_value(self, value, operator):
        scalar = (type(None), bool, numbers.Number, basestring)
        if isinstance(value, list):
            if not all(isinstance(item, scalar) for item in value):
                raise ToolArgumentError("Domain values must be numbers, strings, booleans, null or lists of them.")
        elif not isinstance(value, scalar):
            raise ToolArgumentError("Domain values must be numbers, strings, booleans, null or lists of them.")

    def _check_path(self, env, model, path):
        current = model
        steps = path.split('.')
        for position, step in enumerate(steps):
            fields = self.fields(current)
            if step not in fields:
                raise ToolArgumentError("Unknown or forbidden field in domain: %s" % (path,))
            field = fields[step]
            if position < len(steps) - 1:
                if field.type not in RELATIONAL or not self.permitted(env, field.comodel_name, 'read'):
                    raise ToolArgumentError("Unknown or forbidden field in domain: %s" % (path,))
                current = env[field.comodel_name]

    def check_order(self, model, order):
        if order is None or order == '':
            return None
        if not isinstance(order, basestring):
            raise ToolArgumentError("order must be a string such as 'name asc, id desc'.")
        fields = self.fields(model)
        parts = []
        for chunk in order.split(','):
            words = chunk.split()
            if not words or len(words) > 2:
                raise ToolArgumentError("order must be a string such as 'name asc, id desc'.")
            name = words[0]
            direction = words[1].lower() if len(words) == 2 else 'asc'
            if direction not in ('asc', 'desc') or name not in fields or not fields[name].store:
                raise ToolArgumentError("Cannot order by %r." % (chunk.strip(),))
            parts.append('%s %s' % (name, direction))
        return ', '.join(parts)

    def check_values(self, env, model, values, written):
        if not isinstance(values, dict) or not values:
            raise ToolArgumentError("values must be a non-empty object of field: value.")
        fields = self.fields(model)
        converted = {}
        for name, value in values.items():
            field = fields.get(name)
            if field is None or name in MAGIC_FIELDS:
                raise ToolArgumentError("Unknown or forbidden field on %s: %s" % (model._name, name))
            if field.readonly and not field.inverse and not field.states:
                raise ToolArgumentError("Field %s on %s is read-only." % (name, model._name))
            if field.compute and not field.inverse:
                raise ToolArgumentError("Field %s on %s is computed and cannot be written." % (name, model._name))
            converted[name] = self._convert(env, model, field, value, written)
            written.add('%s.%s' % (model._name, name))
        return converted

    def _convert(self, env, model, field, value, written):
        kind = field.type
        if value is None:
            value = False
        if kind == 'many2one':
            if value is not False and not _is_int(value):
                raise ToolArgumentError("%s expects a record id or null." % field.name)
            self._check_comodel(field)
            return value
        if kind == 'many2many':
            if not isinstance(value, list) or not all(_is_int(item) for item in value):
                raise ToolArgumentError("%s expects a list of record ids (it replaces the current set)." % field.name)
            self._check_comodel(field)
            return [(6, 0, value)]
        if kind == 'one2many':
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ToolArgumentError("%s expects a list of new line objects." % field.name)
            comodel = env[field.comodel_name]
            if not self.permitted(env, field.comodel_name, 'create'):
                raise ToolRefused("This key may not create %s records." % field.comodel_name)
            lines = []
            for item in value:
                line = self.check_values(env, comodel, item, written)
                line.pop(field.inverse_name, None)
                lines.append((0, 0, line))
            return lines
        if value is False:
            return False
        if kind in ('char', 'text', 'html', 'date', 'datetime', 'selection', 'reference') and isinstance(value, basestring):
            return value
        if kind == 'selection' and _is_int(value):
            return value
        if kind == 'boolean' and isinstance(value, bool):
            return value
        if kind == 'integer' and _is_int(value):
            return value
        if kind in ('float', 'monetary') and isinstance(value, numbers.Number) and not isinstance(value, bool):
            return value
        raise ToolArgumentError("Value for %s does not fit a %s field." % (field.name, kind))

    def _check_comodel(self, field):
        if model_is_forbidden(field.comodel_name, self.model_patterns):
            raise ToolArgumentError("Field %s links to a model this endpoint never exposes." % field.name)


def _is_int(value):
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def check_ids(ids, most):
    if not isinstance(ids, list) or not ids or not all(_is_int(item) and item > 0 for item in ids):
        raise ToolArgumentError("ids must be a non-empty list of positive record ids.")
    if len(ids) > most:
        raise ToolArgumentError("At most %s ids per call with this key." % most)
    seen = []
    for item in ids:
        if item not in seen:
            seen.append(item)
    return seen
