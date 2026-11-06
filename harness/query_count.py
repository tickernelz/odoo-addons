import json

import werkzeug.test
import werkzeug.wrappers

import odoo
from odoo.addons.kaitech_mcp_server import endpoint
from odoo.addons.kaitech_mcp_server.tests.common import new_key, new_user

odoo.conf.server_wide_modules = list(odoo.conf.server_wide_modules) + [endpoint.MODULE]
user = new_user(env, 'mcp_query_count')
key, token = new_key(env, user, {'res.partner': {'read', 'create', 'update'}})
key.rate_per_minute = 100000
for index in range(200):
    env['res.partner'].create({'name': 'Count %03d' % index})
env.cr.commit()
client = werkzeug.test.Client(odoo.http.root, werkzeug.wrappers.BaseResponse)


def call(tool, arguments):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}})
    before = odoo.sql_db.sql_counter
    answer = client.post('/mcp/%s' % env.cr.dbname, data=body, headers=[
        ('Content-Type', 'application/json'), ('Authorization', 'Bearer %s' % token)])
    assert answer.status_code == 200 and not json.loads(answer.data)['result']['isError'], answer.data
    return odoo.sql_db.sql_counter - before


report = {}
for _ in range(3):
    call('count_records', {'model': 'res.partner'})
for label, tool, arguments in (
        ('search_records 20', 'search_records', {'model': 'res.partner', 'domain': [['name', 'like', 'Count %']], 'fields': ['name'], 'limit': 20}),
        ('search_records 200', 'search_records', {'model': 'res.partner', 'domain': [['name', 'like', 'Count %']], 'fields': ['name'], 'limit': 200}),
        ('count_records', 'count_records', {'model': 'res.partner'}),
        ('create_record', 'create_record', {'model': 'res.partner', 'values': {'name': 'Counted'}})):
    report[label] = call(tool, arguments)
print('QUERIES ' + json.dumps(report, sort_keys=True))
key.action_revoke()
env.cr.commit()
