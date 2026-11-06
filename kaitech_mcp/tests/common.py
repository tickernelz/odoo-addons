import json
import uuid
from contextlib import contextmanager

import werkzeug.test
import werkzeug.wrappers

import odoo
from odoo import SUPERUSER_ID, api
from odoo.tests import common
from odoo.tools import config

from .. import endpoint, grants, protocol

MODERN = '2026-07-28'


def new_user(env, login, groups=('base.group_user', 'base.group_partner_manager'), lang=None):
    values = {
        'name': login.title(), 'login': login, 'email': '%s@example.com' % login,
        'group_ids': [(6, 0, [env.ref(group).id for group in groups])],
    }
    if lang:
        values['lang'] = lang
    return env['res.users'].with_context(no_reset_password=True).create(values)


def new_key(env, user, permissions, tables=None, **values):
    """Create a key from {model: actions} and {table: actions}; return (key, token)."""
    user = user.with_env(env)
    if tables:
        values.setdefault('allow_sql', True)
        values['sql_table_ids'] = [(0, 0, {
            'table_name': table, 'can_select': 'select' in actions, 'can_insert': 'insert' in actions,
            'can_update': 'update' in actions, 'can_delete': 'delete' in actions,
        }) for table, actions in sorted(tables.items())]
    rows = []
    for model, actions in sorted(permissions.items()):
        rows.append((0, 0, {
            'model_id': env['ir.model'].search([('model', '=', model)]).id,
            'can_read': 'read' in actions, 'can_create': 'create' in actions,
            'can_update': 'update' in actions, 'can_delete': 'delete' in actions, 'can_call': 'call' in actions,
        }))
    values.setdefault('name', '%s client' % user.name)
    key = env['kaitech.mcp.key'].create(dict(values, user_id=user.id, permission_ids=rows))
    return key, key._issue_token()


def set_setting(env, name, value):
    env['ir.config_parameter'].sudo().set_param(grants.PARAM_PREFIX + name, value)


def default_settings(env):
    """Put every MCP setting at its default, whatever the database under test already chose."""
    for name, default in grants.DEFAULTS.items():
        set_setting(env, name, False if isinstance(default, str) else '%s' % default)


def marker():
    """A value no database already holds, for names and cities that tests search on."""
    return 'mcp%s' % uuid.uuid4().hex[:10]


def grant_for(env, key):
    """The Grant the endpoint would build for key."""
    exchange = endpoint.Exchange.__new__(endpoint.Exchange)
    row = dict((name, key[name]) for name in ('id', 'name', 'rate_per_minute', 'writes_per_minute',
                                               'max_read', 'max_write', 'max_delete', 'allow_sql',
                                               'allow_explore', 'allow_call'))
    row['user_id'] = key.user_id.id
    return exchange._grant(env.cr, row)


@common.tagged('post_install', '-at_install')
class EndpointCase(common.HttpCase):
    """Calls /mcp/<db> through Odoo's WSGI root, inside the shared test transaction.

    Use shared_env, never self.env: self.env is another transaction and deadlocks against the endpoint.
    """

    def setUp(self):
        super().setUp()
        self.db = common.get_db_name()
        self._server_wide = list(config['server_wide_modules'])
        if endpoint.MODULE not in self._server_wide:
            config['server_wide_modules'] = self._server_wide + [endpoint.MODULE]
        self.client = werkzeug.test.Client(odoo.http.root, werkzeug.wrappers.Response)
        with self.shared_env() as env:
            default_settings(env)

    def tearDown(self):
        config['server_wide_modules'] = self._server_wide
        super().tearDown()

    @contextmanager
    def shared_env(self, uid=SUPERUSER_ID, context=None):
        """An environment on the cursor the endpoint uses during this test."""
        with self.registry.cursor() as cr:
            yield api.Environment(cr, uid, context or {})

    def post(self, body, token=None, headers=None, path=None):
        data = body if isinstance(body, str) else json.dumps(body)
        sent = {'Content-Type': 'application/json'}
        if token:
            sent['Authorization'] = 'Bearer %s' % token
        sent.update(headers or {})
        response = self.client.post(path or '/mcp/%s' % self.db, data=data, headers=sent.items())
        try:
            decoded = json.loads(response.data) if response.data else None
        except ValueError:
            decoded = response.data
        return response.status_code, decoded, response.headers

    def legacy(self, method, params=None, token=None, ident=1):
        return self.post({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}, token=token)

    def modern(self, method, params=None, token=None, version=MODERN, headers=None):
        params = dict(params or {})
        params['_meta'] = {protocol.PROTOCOL_VERSION_META: version, protocol.CLIENT_CAPABILITIES_META: {},
                           protocol.CLIENT_INFO_META: {'name': 'odoo-tests', 'version': '1'}}
        sent = {protocol.VERSION_HEADER: version, protocol.METHOD_HEADER: method}
        if method == 'tools/call':
            sent[protocol.NAME_HEADER] = params.get('name')
        sent.update(headers or {})
        return self.post({'jsonrpc': '2.0', 'id': 7, 'method': method, 'params': params}, token=token, headers=sent)

    def call(self, token, tool, arguments):
        status, body, _headers = self.modern('tools/call', {'name': tool, 'arguments': arguments}, token=token)
        self.assertEqual(status, 200, body)
        return body['result']

    def last_call(self, key_id):
        with self.shared_env() as env:
            env.cr.execute("SELECT result, reason, tool, model, record_ids, record_count, fields_written, deleted_names "
                           "FROM kaitech_mcp_call WHERE key_id = %s ORDER BY id DESC LIMIT 1", (key_id,))
            return env.cr.dictfetchone()
