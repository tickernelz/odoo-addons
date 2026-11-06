# -*- coding: utf-8 -*-
import json

import odoo

from .. import endpoint, protocol
from .common import MODERN, EndpointCase, new_key, new_user


class TestEndpoint(EndpointCase):

    def setUp(self):
        super(TestEndpoint, self).setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_endpoint_user')
            key, self.token = new_key(env, self.user, {'res.partner': {'read', 'create'}, 'res.country': {'read'},
                                                       'res.partner.category': {'read', 'create'}})
            self.key_id = key.id

    def test_unknown_database_and_malformed_names_share_one_404(self):
        answers = set()
        for path in ('/mcp/no_such_db_zz', '/mcp/', '/mcp', '/mcp/../%s' % self.db, '/mcp/%s/extra' % self.db,
                     '/mcp/postgres'):
            response = self.client.post(path, data='{}', headers=[('Content-Type', 'application/json')])
            self.assertEqual(response.status_code, 404, path)
            answers.add(response.data)
        self.assertEqual(len(answers), 1)

    def test_endpoint_is_off_unless_loaded_server_wide(self):
        odoo.conf.server_wide_modules = [name for name in self._server_wide if name != endpoint.MODULE]
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertEqual(status, 404)

    def test_transport_guards(self):
        response = self.client.get('/mcp/%s' % self.db)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'POST')
        missing = self.client.get('/mcp/no_such_db_zz')
        self.assertEqual((missing.status_code, missing.data), (response.status_code, response.data))
        status, _body, _headers = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, token=self.token,
                                            headers={'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)
        status, _body, _headers = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, token=self.token,
                                            headers={'Origin': 'http://localhost'})
        self.assertEqual(status, 200)
        status, _body, _headers = self.post('x' * (endpoint.MAX_BODY + 1), token=self.token)
        self.assertEqual(status, 413)
        for body in ('not json', '[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]', '{"id": 1, "method": "ping"}'):
            status, answer, _headers = self.post(body, token=self.token)
            self.assertEqual(status, 400, body)
            self.assertIn(answer['error']['code'], (protocol.PARSE_ERROR, protocol.INVALID_REQUEST))

    def test_keys_that_do_not_open_the_door(self):
        with self.shared_env() as env:
            revoked, revoked_token = new_key(env, self.user, {'res.partner': {'read'}})
            revoked.action_revoke()
            expired, expired_token = new_key(env, self.user, {'res.partner': {'read'}},
                                             expires_at='2001-01-01 00:00:00')
            root, root_token = new_key(env, self.user, {'res.partner': {'read'}})
            env.cr.execute("UPDATE kaitech_mcp_key SET user_id = %s WHERE id = %s", (odoo.SUPERUSER_ID, root.id))
        for token in (None, 'nope', self.token[:-2] + 'xx', 'o10mcp_short', revoked_token, expired_token, root_token):
            status, body, headers = self.legacy('tools/list', token=token)
            self.assertEqual(status, 401, token)
            self.assertIn('Bearer', headers['WWW-Authenticate'])
            self.assertEqual(body['error']['code'], protocol.REFUSED_HERE)
        self.assertEqual(self.last_call(revoked.id)['result'], 'refused')
        self.assertEqual(self.last_call(revoked.id)['reason'], 'revoked')
        self.assertEqual(self.last_call(root.id)['reason'], 'superuser')

    def test_handshake_era(self):
        status, body, _headers = self.legacy('initialize', {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'Claude', 'version': '1'}},
            token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(body['result']['serverInfo']['name'], protocol.SERVER_INFO['name'])
        self.assertNotIn('resultType', body['result'])
        status, body, _headers = self.legacy('initialize', {'protocolVersion': '1999-01-01'}, token=self.token)
        self.assertEqual(body['result']['protocolVersion'], protocol.HANDSHAKE_VERSIONS[0])
        status, body, _headers = self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, token=self.token)
        self.assertEqual((status, body), (202, None))
        status, body, _headers = self.legacy('resources/list', token=self.token)
        self.assertEqual(body['error']['code'], protocol.METHOD_NOT_FOUND)
        self.assertEqual(self.last_call(self.key_id)['result'], 'invalid_arguments')

    def test_modern_era(self):
        status, body, _headers = self.modern('server/discover', token=self.token)
        self.assertEqual(status, 200)
        result = body['result']
        self.assertIn(MODERN, result['supportedVersions'])
        self.assertEqual(result['resultType'], 'complete')
        self.assertEqual(result['_meta'][protocol.SERVER_INFO_META]['name'], protocol.SERVER_INFO['name'])
        status, body, _headers = self.modern('tools/list', token=self.token)
        self.assertEqual(body['result']['resultType'], 'complete')
        status, body, _headers = self.modern('tools/list', token=self.token, headers={protocol.METHOD_HEADER: 'tools/call'})
        self.assertEqual((status, body['error']['code']), (400, protocol.HEADER_MISMATCH))
        status, body, _headers = self.modern('tools/list', token=self.token, version='2027-01-01')
        self.assertEqual((status, body['error']['code']), (400, protocol.UNSUPPORTED_PROTOCOL_VERSION))
        self.assertEqual(body['error']['data']['supported'], list(protocol.MODERN_VERSIONS))
        status, body, _headers = self.modern('tools/call', {'name': 'list_models', 'arguments': {}}, token=self.token,
                                             headers={protocol.NAME_HEADER: 'delete_records'})
        self.assertEqual(body['error']['code'], protocol.HEADER_MISMATCH)
        status, body, _headers = self.modern('initialize', token=self.token)
        self.assertEqual(body['error']['code'], protocol.UNSUPPORTED_PROTOCOL_VERSION)

    def test_tools_list_follows_key_permissions(self):
        status, body, _headers = self.legacy('tools/list', token=self.token)
        names = [tool['name'] for tool in body['result']['tools']]
        self.assertEqual(names, ['list_models', 'get_model_fields', 'search_records', 'count_records',
                                 'read_records', 'create_record'])
        for tool in body['result']['tools']:
            self.assertEqual(tool['inputSchema']['type'], 'object')
            self.assertNotIn('action', tool)

    def test_session_cookie_is_ignored_and_no_session_is_stored(self):
        store = odoo.http.root.session_store
        before = set(store.list())
        self.client.set_cookie('localhost', 'session_id', 'f' * 40)
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(set(store.list()), before)

    def test_other_routes_are_untouched(self):
        response = self.client.post('/web/webclient/version_info', data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call', 'params': {}, 'id': 1}), headers=[('Content-Type', 'application/json')])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['result']['server_version_info'][0], 10)
        response = self.client.get('/web/login?db=%s' % self.db, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_rate_limit_counts_in_the_database(self):
        with self.shared_env() as env:
            env['kaitech.mcp.key'].browse(self.key_id).write({'rate_per_minute': 3})
        for _round in range(3):
            self.assertEqual(self.legacy('tools/list', token=self.token)[0], 200)
        for _round in range(2):
            status, body, headers = self.legacy('tools/list', token=self.token)
            self.assertEqual(status, 429)
            self.assertEqual(body['error']['code'], protocol.OVER_THE_LIMIT)
            self.assertTrue(int(headers['Retry-After']) >= 1)
        self.assertEqual(self.legacy('ping', token=self.token)[0], 200)
        with self.shared_env() as env:
            env.cr.execute("SELECT result, count(*) FROM kaitech_mcp_call WHERE key_id = %s GROUP BY result", (self.key_id,))
            self.assertEqual(dict(env.cr.fetchall()), {'ok': 3, 'limited': 1})

    def test_write_rate_is_separate(self):
        with self.shared_env() as env:
            env['kaitech.mcp.key'].browse(self.key_id).write({'writes_per_minute': 1})
        self.assertFalse(self.call(self.token, 'create_record', {'model': 'res.partner.category',
                                                                  'values': {'name': 'MCP One'}})['isError'])
        status, _body, _headers = self.modern('tools/call', {'name': 'create_record', 'arguments': {
            'model': 'res.partner.category', 'values': {'name': 'MCP Two'}}}, token=self.token)
        self.assertEqual(status, 429)
        self.assertFalse(self.call(self.token, 'count_records', {'model': 'res.partner'})['isError'])
