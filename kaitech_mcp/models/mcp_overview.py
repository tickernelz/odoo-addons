import json
from contextlib import closing

import psycopg2
import requests

import odoo
from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import config

from .. import endpoint

MAX_DATABASES = 50
OUTCOMES = [
    ('ok', 'Reachable: routing and database selection work'),
    ('not_server_wide', 'Not found: the module is not loaded server-wide'),
    ('not_found', 'Not found: the database is not served at this address'),
    ('unreachable', 'Unreachable'),
    ('unexpected', 'Unexpected answer'),
]
LAST_TEST = 'kaitech_mcp.last_endpoint_test'
SELF_TEST_VERSION = '2026-07-28'


class McpOverview(models.TransientModel):
    _name = 'kaitech.mcp.overview'
    _description = 'MCP Overview'

    endpoint_url = fields.Char('Endpoint for this database', compute='_compute_overview')
    server_wide = fields.Boolean('Loaded server-wide', compute='_compute_overview')
    config_line = fields.Char('Line for odoo.conf', compute='_compute_overview')
    active_keys = fields.Integer('Active keys', compute='_compute_overview')
    calls_24h = fields.Integer('Calls (24 h)', compute='_compute_overview')
    refused_24h = fields.Integer('Refused or limited (24 h)', compute='_compute_overview')
    errors_24h = fields.Integer('Errors (24 h)', compute='_compute_overview')
    last_test_result = fields.Selection(OUTCOMES, 'Last endpoint test', compute='_compute_overview')
    last_test_detail = fields.Char('Details', compute='_compute_overview')
    last_test_at = fields.Datetime('Tested on', compute='_compute_overview')
    database_ids = fields.One2many('kaitech.mcp.overview.database', 'overview_id', 'Databases on this server',
                                   readonly=True)
    databases_hidden = fields.Boolean('Database list hidden', readonly=True)

    @api.model
    def endpoint_url_for(self, db):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or 'http://localhost:8069'
        return '%s/mcp/%s' % (base.rstrip('/'), db)

    def _compute_overview(self):
        cr = self.env.cr
        cr.execute("""
            SELECT sum(CASE WHEN result != 'limited' THEN 1 ELSE 0 END),
                   sum(CASE WHEN result IN ('refused', 'limited') THEN 1 ELSE 0 END),
                   sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END)
              FROM kaitech_mcp_call WHERE create_date > (now() AT TIME ZONE 'UTC') - interval '24 hours'""")
        calls, refused, errors = cr.fetchone()
        active = self.env['kaitech.mcp.key'].search_count([('state', 'in', ['active', 'expiring'])])
        last = json.loads(self.env['ir.config_parameter'].sudo().get_param(LAST_TEST) or '{}')
        loaded = [name for name in config['server_wide_modules'] if name != endpoint.MODULE]
        for overview in self:
            overview.endpoint_url = self.endpoint_url_for(cr.dbname)
            overview.server_wide = endpoint.server_wide()
            overview.config_line = 'server_wide_modules = %s' % ','.join(loaded + [endpoint.MODULE])
            overview.active_keys = active
            overview.calls_24h = calls or 0
            overview.refused_24h = refused or 0
            overview.errors_24h = errors or 0
            overview.last_test_result = last.get('result')
            overview.last_test_detail = last.get('detail')
            overview.last_test_at = last.get('at') and fields.Datetime.to_datetime(last['at'])

    def _databases(self):
        """Databases this host serves, or None when the server hides its database list (list_db = False)."""
        if not config['list_db']:
            return None
        names = odoo.service.db.list_dbs(True)
        if odoo.http.request:
            names = odoo.http.db_filter(names)
        rows = []
        for name in names[:MAX_DATABASES]:
            installed = False
            try:
                with closing(odoo.sql_db.db_connect(name).cursor()) as cr:
                    cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (endpoint.MODULE,))
                    found = cr.fetchone()
                    installed = bool(found) and found[0] == 'installed'
            except psycopg2.Error:
                pass
            rows.append({'name': name, 'installed': installed, 'current': name == self.env.cr.dbname,
                         'endpoint_url': self.endpoint_url_for(name) if installed else False})
        return rows

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group('kaitech_mcp.group_mcp_manager'):
            raise AccessError(self.env._("Only MCP managers can open the MCP overview."))
        return super().create(vals_list)

    @api.model
    def action_open(self):
        databases = self._databases()
        overview = self.create({
            'databases_hidden': databases is None,
            'database_ids': [fields.Command.create(line) for line in databases or []],
        })
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': overview.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current', 'name': self.env._('MCP Overview'),
        }

    def action_test_endpoint(self):
        """POST server/discover without a key to this database's public URL: 401 proves routing works."""
        self.ensure_one()
        result, detail = self._probe(self.endpoint_url)
        self.env['ir.config_parameter'].sudo().set_param(LAST_TEST, json.dumps(
            {'result': result, 'detail': detail, 'at': fields.Datetime.to_string(fields.Datetime.now())}))
        return self.action_open()

    def action_new_key(self):
        return self.env['ir.actions.act_window']._for_xml_id('kaitech_mcp.action_mcp_key_wizard')

    @api.model
    def _post(self, url, method, token=None, params=None):
        """POST one MCP request; return (HTTP status, decoded JSON or None, failure text or None)."""
        params = dict(params or {})
        params['_meta'] = {'io.modelcontextprotocol/protocolVersion': SELF_TEST_VERSION,
                           'io.modelcontextprotocol/clientCapabilities': {},
                           'io.modelcontextprotocol/clientInfo': {'name': 'odoo-mcp-self-test', 'version': '1'}}
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'Mcp-Protocol-Version': SELF_TEST_VERSION, 'Mcp-Method': method}
        if token:
            headers['Authorization'] = 'Bearer %s' % token
        try:
            answer = requests.post(url, data=body, headers=headers, timeout=10, allow_redirects=False)
        except requests.RequestException as failure:
            return None, None, self.env._("%(url)s could not be reached: %(reason)s", url=url, reason=failure)
        try:
            decoded = answer.json()
        except ValueError:
            decoded = None
        return answer.status_code, decoded, None

    @api.model
    def _probe(self, url):
        status, _payload, failure = self._post(url, 'server/discover')
        if failure:
            return 'unreachable', failure
        if status == 401:
            return 'ok', self.env._("HTTP 401 without a key: the endpoint answers for this database.")
        if status == 404:
            if not endpoint.server_wide():
                return 'not_server_wide', self.env._("HTTP 404: add the line shown above to odoo.conf and restart Odoo.")
            return 'not_found', self.env._("HTTP 404: this address does not reach this database. "
                                           "Check dbfilter, db_name and the reverse proxy for /mcp/.")
        return 'unexpected', self.env._("HTTP %(status)s from %(url)s", status=status, url=url)


class McpOverviewDatabase(models.TransientModel):
    _name = 'kaitech.mcp.overview.database'
    _description = 'MCP Overview Database'

    overview_id = fields.Many2one('kaitech.mcp.overview', ondelete='cascade')
    name = fields.Char('Database')
    installed = fields.Boolean('MCP module installed')
    current = fields.Boolean('This database')
    endpoint_url = fields.Char('Endpoint')
