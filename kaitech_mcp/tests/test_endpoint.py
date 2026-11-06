import json
import logging

import odoo
from odoo.tools import config

from .. import endpoint, protocol
from .common import MODERN, EndpointCase, new_key, new_user


class TestEndpoint(EndpointCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_endpoint_user')
            key, self.token = new_key(env, self.user, {'res.partner': {'read', 'create'}, 'res.country': {'read'}})
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
        config['server_wide_modules'] = [name for name in self._server_wide if name != endpoint.MODULE]
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertEqual(status, 404)

    def test_transport_guards(self):
        response = self.client.get('/mcp/%s' % self.db)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'POST')
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

    def test_other_methods_get_one_answer_whatever_the_database(self):
        answers = set()
        for method in ('GET', 'PUT', 'DELETE', 'OPTIONS'):
            for path in ('/mcp/%s' % self.db, '/mcp/no_such_db_zz', '/mcp/postgres', '/mcp/'):
                response = self.client.open(path, method=method)
                self.assertEqual(response.status_code, 405, (method, path))
                answers.add((response.data, response.headers.get('Allow')))
        self.assertEqual(len(answers), 1)

    def test_origin_behind_a_reverse_proxy(self):
        proxy_mode = config['proxy_mode']
        config['proxy_mode'] = True
        self.addCleanup(config.__setitem__, 'proxy_mode', proxy_mode)
        forwarded = {'X-Forwarded-Host': 'erp.example.com', 'X-Forwarded-Proto': 'https'}
        ping = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
        status, _body, _headers = self.post(ping, token=self.token, headers=dict(forwarded, Origin='https://erp.example.com'))
        self.assertEqual(status, 200)
        status, _body, _headers = self.post(ping, token=self.token, headers=dict(forwarded, Origin='https://evil.example'))
        self.assertEqual(status, 403)

    def test_keys_that_do_not_open_the_door(self):
        with self.shared_env() as env:
            revoked, revoked_token = new_key(env, self.user, {'res.partner': {'read'}})
            revoked.action_revoke()
            expired, expired_token = new_key(env, self.user, {'res.partner': {'read'}},
                                             expires_at='2001-01-01 00:00:00')
            root, root_token = new_key(env, self.user, {'res.partner': {'read'}})
            env.cr.execute("UPDATE kaitech_mcp_key SET user_id = %s WHERE id = %s", (odoo.SUPERUSER_ID, root.id))
        for token in (None, 'nope', self.token[:-2] + 'xx', 'o19mcp_short', revoked_token, expired_token, root_token):
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
        status, body, _headers = self.legacy('prompts/list', token=self.token)
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
        self.client.set_cookie('session_id', 'f' * 40, domain='localhost')
        status, body, _headers = self.legacy('tools/list', token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(set(store.list()), before)

    def test_other_routes_are_untouched(self):
        response = self.url_open('/web/webclient/version_info', data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call', 'params': {}, 'id': 1}), headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['server_version_info'][0], 19)
        response = self.url_open('/web/login')
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
        self.assertFalse(self.call(self.token, 'create_record', {'model': 'res.partner', 'values': {'name': 'One'}})['isError'])
        status, _body, _headers = self.modern('tools/call', {'name': 'create_record', 'arguments': {
            'model': 'res.partner', 'values': {'name': 'Two'}}}, token=self.token)
        self.assertEqual(status, 429)
        self.assertFalse(self.call(self.token, 'count_records', {'model': 'res.partner'})['isError'])


class TestKeyHeaders(EndpointCase):
    """One key read from three headers: first one carrying a key decides, nothing else is tried."""

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.user = new_user(env, 'mcp_header_user')
            key, self.token = new_key(env, self.user, {'res.country': {'read'}})
            self.key_id = key.id

    def headers_for(self, name, value):
        return {name: 'Bearer %s' % value if name == 'Authorization' else value}

    def ping(self, headers=None):
        return self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, headers=headers)

    def test_the_key_may_arrive_in_any_of_the_three_headers(self):
        answers = set()
        for name in endpoint.KEY_HEADERS:
            status, body, _headers = self.legacy('tools/list', headers=self.headers_for(name, self.token))
            self.assertEqual(status, 200, name)
            answers.add(json.dumps(body, sort_keys=True))
        self.assertEqual(len(answers), 1)

    def test_custom_headers_take_a_bare_key_or_a_bearer_prefix(self):
        for name in ('X-API-Key', 'x-api-key', 'X-Auth-Token'):
            for value in (self.token, 'Bearer %s' % self.token, 'bearer %s' % self.token):
                status, body, _headers = self.ping({name: value})
                self.assertEqual(status, 200, (name, value))

    def test_a_key_never_comes_from_the_query_string_or_the_body(self):
        for query in ('api_key', 'token', 'key', 'x-api-key'):
            status, body, _headers = self.post(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                path='/mcp/%s?%s=%s' % (self.db, query, self.token))
            self.assertEqual(status, 401, query)
            self.assertEqual(body['error']['code'], protocol.REFUSED_HERE)
        for body in ({'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': {'api_key': self.token}},
                     {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'api_key': self.token}):
            status, _body, _headers = self.post(body)
            self.assertEqual(status, 401)

    def test_the_first_header_carrying_a_key_decides(self):
        wrong = self.token[:-2] + 'xx'
        status, _body, _headers = self.ping({'Authorization': 'Bearer %s' % wrong, 'X-API-Key': self.token})
        self.assertEqual(status, 401)
        status, _body, _headers = self.ping({'Authorization': 'Bearer %s' % self.token, 'X-API-Key': wrong})
        self.assertEqual(status, 200)
        status, _body, _headers = self.ping({'X-API-Key': wrong, 'X-Auth-Token': self.token})
        self.assertEqual(status, 401)

    def test_an_authorization_without_a_bearer_value_holds_no_key(self):
        for authorization in ('Basic dXNlcjpwYXNz', 'Bearer', 'Bearer   ', self.token):
            status, _body, _headers = self.ping({'Authorization': authorization, 'X-API-Key': self.token})
            self.assertEqual(status, 200, authorization)
            status, _body, _headers = self.ping({'Authorization': authorization})
            self.assertEqual(status, 401, authorization)

    def test_a_blank_header_carries_no_key(self):
        for name in ('X-API-Key', 'X-Auth-Token'):
            for value in ('', '   '):
                status, body, _headers = self.ping({name: value})
                self.assertEqual(status, 401, (name, value))
                self.assertEqual(body['error']['code'], protocol.REFUSED_HERE)
        status, _body, _headers = self.ping({'X-API-Key': '  ', 'X-Auth-Token': self.token})
        self.assertEqual(status, 200)

    def test_every_refusal_answers_the_same_way(self):
        refused = set()
        for name in endpoint.KEY_HEADERS:
            for value in (None, self.token[:-2] + 'xx'):
                status, body, headers = self.ping({} if value is None else self.headers_for(name, value))
                self.assertEqual(status, 401, (name, value))
                refused.add((status, json.dumps(body, sort_keys=True), headers['WWW-Authenticate']))
        self.assertEqual(len(refused), 1)

    def test_every_refused_key_stays_refused_whichever_header_carries_it(self):
        with self.shared_env() as env:
            revoked, revoked_token = new_key(env, self.user, {'res.country': {'read'}})
            revoked.action_revoke()
            _expired, expired_token = new_key(env, self.user, {'res.country': {'read'}},
                                              expires_at='2001-01-01 00:00:00')
            root, root_token = new_key(env, self.user, {'res.country': {'read'}})
            env.cr.execute("UPDATE kaitech_mcp_key SET user_id = %s WHERE id = %s", (odoo.SUPERUSER_ID, root.id))
            idle = new_user(env, 'mcp_header_idle')
            _idle_key, idle_token = new_key(env, idle, {'res.country': {'read'}})
            env.cr.execute("UPDATE res_users SET active = false WHERE id = %s", (idle.id,))
        elsewhere = 'o19mcp_%s' % ('a' * 43)
        for name in endpoint.KEY_HEADERS:
            for token in (revoked_token, expired_token, root_token, idle_token, elsewhere):
                status, body, _headers = self.legacy('tools/list', headers=self.headers_for(name, token))
                self.assertEqual(status, 401, (name, token))
                self.assertEqual(body['error']['code'], protocol.REFUSED_HERE)

    def test_a_refused_key_is_never_written_to_the_log(self):
        written = []
        collector = logging.Handler()
        collector.emit = lambda record: written.append(record.getMessage())
        endpoint._logger.addHandler(collector)
        self.addCleanup(endpoint._logger.removeHandler, collector)
        wrong = self.token[:-2] + 'xx'
        for name in endpoint.KEY_HEADERS:
            for value in (wrong, 'o19mcp_unknown_key_value_zz', 'Basic dXNlcjpwYXNz'):
                status, _body, _headers = self.ping({name: value})
                self.assertEqual(status, 401, (name, value))
        trail = '\n'.join(written)
        self.assertIn('refused', trail, trail)
        for secret in (self.token, wrong, 'unknown_key_value_zz', 'dXNlcjpwYXNz', 'Basic'):
            self.assertNotIn(secret, trail)
