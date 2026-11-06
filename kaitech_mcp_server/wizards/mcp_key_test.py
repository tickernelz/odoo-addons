# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

from .. import keys

RESULTS = [
    ('ok', 'The key works'),
    ('wrong_key', 'This is not the key of this record'),
    ('refused', 'The endpoint refused the key'),
    ('not_found', 'The endpoint was not found'),
    ('unreachable', 'The endpoint could not be reached'),
    ('unexpected', 'Unexpected answer'),
]


class McpKeyTest(models.TransientModel):
    _name = 'kaitech.mcp.key.test'
    _description = 'Test MCP Key'

    key_id = fields.Many2one('kaitech.mcp.key', 'Key', required=True, readonly=True)
    token = fields.Char('Paste the key', help="The key is checked and then forgotten; it is never stored.")
    result = fields.Selection(RESULTS, 'Result', readonly=True)
    detail = fields.Text('What the key can do', readonly=True)

    @api.multi
    def action_run(self):
        self.ensure_one()
        token = (self.token or '').strip()
        values = {'token': False}
        if not keys.matches(token, self.key_id.sudo().key_hash):
            values.update(result='wrong_key', detail=_("The pasted text does not match this key. Keys are shown only once; "
                                                       "rotate the key if it was lost."))
            self.write(values)
            return self._reopen()
        overview = self.env['kaitech.mcp.overview']
        url = overview.endpoint_url_for(self.env.cr.dbname)
        status, payload, failure = overview._post(url, 'tools/list', token=token)
        if failure:
            values.update(result='unreachable', detail=failure)
        elif status == 200 and payload and 'result' in payload:
            names = [tool['name'] for tool in payload['result'].get('tools', [])]
            values.update(result='ok', detail=names and _("Tools this key can use:\n%s") % '\n'.join(names)
                          or _("The key is accepted but has no model permissions yet."))
        elif status == 401:
            values.update(result='refused', detail=_("HTTP 401: the key is revoked, expired, or its user is archived."))
        elif status == 404:
            values.update(result='not_found', detail=_("HTTP 404: run Test endpoint on MCP Overview for the fix."))
        else:
            values.update(result='unexpected', detail=_("HTTP %s") % status)
        self.write(values)
        return self._reopen()

    @api.multi
    def _reopen(self):
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new', 'name': _('Test MCP key'),
        }
