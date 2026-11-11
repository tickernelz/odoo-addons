from odoo.tools import config, mute_logger

from .. import hooks
from ..services.sse_manager import session_manager
from .common import McpCase, new_token, new_user

NOT_FOUND = {'error': 'not_found', 'error_description': 'No MCP endpoint here.'}


class TestEndpoint(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_endpoint_user')
            self.token, self.raw = new_token(env, self.user)

    def server_wide(self):
        listed = list(config['server_wide_modules'])
        config['server_wide_modules'] = listed + [hooks.MODULE]
        self.addCleanup(config.__setitem__, 'server_wide_modules', listed)
        hooks.post_load()

    def test_the_database_header_selects_the_database(self):
        status, body = self.rpc('tools/list', raw=self.raw)
        self.assertEqual(status, 200, body)
        self.assertIn('odoo_search_read', [tool['name'] for tool in body['result']['tools']])

    def test_the_database_path_must_name_the_serving_database(self):
        status, _body = self.rpc('tools/list', raw=self.raw, path='/mcp/%s' % self.db,
                                 headers={'X-Odoo-Database': self.db})
        self.assertEqual(status, 200)
        status, body = self.rpc('tools/list', raw=self.raw, path='/mcp/other_%s' % self.db,
                                headers={'X-Odoo-Database': self.db})
        self.assertEqual((status, body), (404, NOT_FOUND))

    def test_the_token_may_come_in_any_key_header(self):
        for headers in ({'X-API-Key': self.raw}, {'X-Auth-Token': self.raw}, {'X-API-Key': 'Bearer %s' % self.raw},
                        {'Authorization': 'bearer %s' % self.raw}):
            status, body = self.rpc('tools/list', headers=headers)
            self.assertEqual(status, 200, (headers, body))
        for headers in ({'Authorization': 'Basic %s' % self.raw}, {'X-API-Key': 'wrong'}, {}):
            status, _body = self.rpc('tools/list', headers=headers)
            self.assertEqual(status, 401, headers)

    def test_server_wide_mode_routes_the_database_path_without_a_header(self):
        self.server_wide()
        status, body = self.rpc('tools/list', raw=self.raw, path='/mcp/%s' % self.db)
        self.assertEqual(status, 200, body)
        answers = [self.rpc('tools/list', raw=self.raw, path='/mcp/%s' % name)
                   for name in ('nope_%s' % self.db, 'postgres', 'template1')]
        self.assertEqual(answers, [(404, NOT_FOUND)] * 3)

    def test_server_wide_mode_ignores_a_session_cookie(self):
        self.server_wide()
        status, _body = self.rpc('tools/list', raw=self.raw, path='/mcp/%s' % self.db,
                                 headers={'Cookie': 'session_id=%s' % ('x' * 84)})
        self.assertEqual(status, 200)

    def test_database_names_are_read_from_the_path(self):
        self.assertIsNone(hooks.database_of({'PATH_INFO': '/mcp/sse'}))
        self.assertIsNone(hooks.database_of({'PATH_INFO': '/mcp/messages'}))
        self.assertEqual(hooks.database_of({'PATH_INFO': '/mcp/db1/sse'}), 'db1')
        self.assertIsNone(hooks.database_of({'PATH_INFO': '/mcp/../web'}))

    def test_an_sse_session_never_crosses_databases(self):
        session = session_manager.create(self.user.id, 'other_%s' % self.db)
        self.addCleanup(session_manager.remove, session.session_id)
        with mute_logger('odoo.addons.kaitech_mcp_server.controllers.mcp_controller'):
            status, body = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                                     path='/mcp/messages?session_id=%s' % session.session_id,
                                     headers={'X-Odoo-Database': self.db})
        self.assertEqual(status, 404, body)
        self.assertTrue(session._queue.empty())

    def test_the_http_session_cache_is_per_database(self):
        session_manager.set_http_session_db_id('other_%s' % self.db, self.token.id, 999999)
        self.assertIsNone(session_manager.get_http_session_db_id(self.db, self.token.id))
        status, _body = self.rpc('tools/list', raw=self.raw)
        self.assertEqual(status, 200)
        with self.shared_env() as env:
            session_id = session_manager.get_http_session_db_id(self.db, self.token.id)
            self.assertEqual(env['mcp.session'].browse(session_id).token_id.id, self.token.id)
