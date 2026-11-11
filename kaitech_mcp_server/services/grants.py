"""What one MCP token may touch on the SQL and Explore paths: settings, forbidden tables and columns."""

PARAM_PREFIX = 'mcp_server.sql_'

DEFAULTS = {
    'enabled': False,
    'delete_enabled': False,
    'statement_timeout_ms': 5000,
    'forbidden_fields': '',
    'forbidden_tables': '',
}

MAX_READ = 200
MAX_WRITE = 100
MAX_DELETE = 20

FORBIDDEN_FIELDS = (
    'password', 'new_password', 'signup_token', 'oauth_access_token', 'access_token', 'totp_secret',
)
ACTIONS = ('select', 'insert', 'update', 'delete')


class ToolArgumentError(ValueError):
    """The client sent arguments this token may not use or that are malformed."""


class ToolRefused(PermissionError):
    """The token has no permission for what was asked."""


def split_list(text):
    if not text:
        return []
    return [item.strip() for item in text.replace('\n', ',').split(',') if item.strip()]


def read_settings(env):
    """Settings as the ORM sees them, including writes not yet flushed in this transaction."""
    env['ir.config_parameter'].flush_model(['key', 'value'])
    cr = env.cr
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


def forbidden_field_rules(settings):
    """Return (names forbidden on every model, {model: names forbidden on that model})."""
    everywhere = set(FORBIDDEN_FIELDS)
    per_model = {}
    for item in split_list(settings.get('forbidden_fields')):
        model, dot, name = item.rpartition('.')
        if dot and model:
            per_model.setdefault(model, set()).add(name)
        else:
            everywhere.add(item)
    return everywhere, per_model


class Grant(object):
    """SQL and Explore permissions of one token for one request, read once from the database."""

    def __init__(self, token, settings):
        self.token_id = token.id
        self.scope = token.scope or 'write'
        self.settings = settings
        self.delete_enabled = settings['delete_enabled']
        self.fields_everywhere, self.fields_per_model = forbidden_field_rules(settings)
        self.sql_open = bool(settings['enabled'] and token.allow_sql)
        self.explore = bool(token.allow_explore)
        self.tables = dict((row.name, {
            'select': row.can_select, 'insert': row.can_insert,
            'update': row.can_update, 'delete': row.can_delete,
        }) for row in token.sql_table_ids)
        self._existing_tables = None

    @classmethod
    def of(cls, env):
        """The grant of the token serving this request, or None outside an MCP call."""
        token_id = env.context.get('mcp_token_id')
        if not token_id:
            return None
        token = env['mcp.token'].sudo().browse(token_id).exists()
        if not token:
            return None
        return cls(token, read_settings(env))

    def table_permitted(self, cr, table, action):
        from .sql import database_tables, table_is_forbidden
        row = self.tables.get(table)
        if not self.sql_open or not row or not row[action]:
            return False
        if action != 'select' and self.scope == 'read':
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
                      if any(self.table_permitted(cr, name, action) for action in ACTIONS))

    def require_table(self, cr, table, action):
        if not isinstance(table, str) or not self.table_permitted(cr, table, action):
            if isinstance(table, str) and self.table_permitted(cr, table, 'select'):
                raise ToolRefused("This token may not %s rows of %s." % (action, table))
            raise ToolArgumentError("Unknown table: %s" % (table,))

    def columns_forbidden_on(self, table):
        forbidden = set(self.fields_everywhere)
        for model, names in self.fields_per_model.items():
            if model == table or model.replace('.', '_') == table:
                forbidden |= names
        return forbidden

    def max_read(self):
        return MAX_READ

    def max_write(self):
        return MAX_WRITE

    def max_delete(self):
        return MAX_DELETE

    def model_access(self, env, model_name):
        """What this token allows on model_name through the record tools, whatever the Odoo user may do."""
        restrictions = env.context.get('mcp_restrictions') or {}
        denied = restrictions.get('denied_models') or set()
        allowed = restrictions.get('allowed_models') or set()
        open_model = model_name not in denied and (not allowed or model_name in allowed)
        writable = open_model and self.scope in ('write', 'admin')
        return {
            'read': open_model,
            'create': writable,
            'update': writable,
            'delete': open_model and self.scope == 'admin',
            'call': open_model and self.scope == 'admin',
        }
