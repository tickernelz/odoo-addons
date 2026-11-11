import json
import uuid
from contextlib import contextmanager

import werkzeug.test
import werkzeug.wrappers

import odoo
from odoo import SUPERUSER_ID, api
from odoo.tests import common

from ..services import grants


def new_user(env, login, groups=('base.group_user', 'base.group_partner_manager'), lang=None):
    values = {
        'name': login.title(), 'login': login, 'email': '%s@example.com' % login, 'password': login * 2,
        'group_ids': [(6, 0, [env.ref(group).id for group in groups])],
    }
    if lang:
        values['lang'] = lang
    return env['res.users'].with_context(no_reset_password=True).create(values)


def new_token(env, user, scope='write', tables=None, **values):
    """Issue a token for user with {table: actions} SQL rows and extra field values; return (token, raw)."""
    user = user.with_env(env)
    Token = env['mcp.token'].sudo()
    raw = Token.issue(user.id, client_name='%s client' % user.name, token_type='pat', scope=scope)
    token = Token.search([('user_id', '=', user.id)], order='id desc', limit=1)
    if tables:
        values.setdefault('allow_sql', True)
        values['sql_table_ids'] = [(0, 0, {
            'name': table, 'can_select': 'select' in actions, 'can_insert': 'insert' in actions,
            'can_update': 'update' in actions, 'can_delete': 'delete' in actions,
        }) for table, actions in sorted(tables.items())]
    if values:
        token.write(values)
    return token, raw


def set_setting(env, name, value):
    env['ir.config_parameter'].sudo().set_param(grants.PARAM_PREFIX + name, value)


def default_settings(env):
    """Put every SQL setting at its default, whatever the database under test already chose."""
    for name, default in grants.DEFAULTS.items():
        set_setting(env, name, False if isinstance(default, str) else '%s' % default)


def marker():
    """A value no database already holds, for names and cities that tests search on."""
    return 'mcp%s' % uuid.uuid4().hex[:10]


@common.tagged('post_install', '-at_install')
class McpCase(common.HttpCase):
    """Calls the MCP endpoint through Odoo's WSGI root, inside the shared test transaction.

    Use shared_env, never self.env, for data the endpoint must see: self.env is another transaction.
    """

    def setUp(self):
        super().setUp()
        self.db = common.get_db_name()
        self.client = werkzeug.test.Client(odoo.http.root, werkzeug.wrappers.Response)
        with self.shared_env() as env:
            default_settings(env)

    @contextmanager
    def shared_env(self, uid=SUPERUSER_ID, context=None):
        """An environment on the cursor the endpoint uses during this test."""
        with self.registry.cursor() as cr:
            yield api.Environment(cr, uid, context or {})

    def post(self, body, raw=None, headers=None, path=None):
        data = body if isinstance(body, str) else json.dumps(body)
        sent = {'Content-Type': 'application/json'}
        if path is None:
            path = '/mcp'
            sent['X-Odoo-Database'] = self.db
        if raw:
            sent['Authorization'] = 'Bearer %s' % raw
        sent.update(headers or {})
        sent = dict((name, value) for name, value in sent.items() if value is not None)
        self.cr.flush()
        self.cr.clear()
        with self.allow_requests(all_requests=True):
            response = self.client.post(path, data=data, headers=list(sent.items()),
                                        environ_base={'REMOTE_ADDR': '127.0.0.1'})
        try:
            decoded = json.loads(response.data) if response.data else None
        except ValueError:
            decoded = response.data
        return response.status_code, decoded

    def rpc(self, method, params=None, raw=None, **options):
        return self.post({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}, raw=raw, **options)

    def tool_names(self, raw):
        status, body = self.rpc('tools/list', raw=raw)
        self.assertEqual(status, 200, body)
        return [tool['name'] for tool in body['result']['tools']]

    def call(self, raw, tool, arguments):
        """The tool answer as {'isError': bool, 'structuredContent': payload, or {'error': text}}."""
        status, body = self.rpc('tools/call', {'name': tool, 'arguments': arguments}, raw=raw)
        self.assertEqual(status, 200, body)
        result = body['result']
        text = result['content'][0]['text']
        if result['isError']:
            return {'isError': True, 'structuredContent': {'error': text}}
        return {'isError': False, 'structuredContent': json.loads(text)}

    def last_call(self, user):
        with self.shared_env() as env:
            env.cr.execute("SELECT tool_name AS tool, odoo_model AS model, record_count, is_error, statement, dry_run "
                           "FROM mcp_session_log WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user.id,))
            return env.cr.dictfetchone()
