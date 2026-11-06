# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

from .. import grants

_logger = logging.getLogger(__name__)

RESULTS = [
    ('ok', 'OK'),
    ('refused', 'Refused'),
    ('limited', 'Rate limited'),
    ('error', 'Error'),
    ('invalid_arguments', 'Invalid arguments'),
]


class McpCall(models.Model):
    _name = 'kaitech.mcp.call'
    _description = 'MCP Call'
    _order = 'create_date desc, id desc'
    _rec_name = 'method'

    key_id = fields.Many2one('kaitech.mcp.key', 'Key', ondelete='set null', readonly=True)
    key_name = fields.Char('Client', readonly=True)
    user_id = fields.Many2one('res.users', 'Acted as', ondelete='set null', readonly=True)
    method = fields.Char('Method', readonly=True)
    tool = fields.Char('Tool', readonly=True)
    is_write = fields.Boolean('Write', readonly=True)
    model = fields.Char('Model', readonly=True)
    record_ids = fields.Char('Record ids', readonly=True)
    record_count = fields.Integer('Records', readonly=True)
    fields_written = fields.Char('Fields written', readonly=True)
    deleted_names = fields.Text('Deleted records', readonly=True)
    statement = fields.Text('SQL statement', readonly=True, help="Values are bound parameters and never stored.")
    dry_run = fields.Boolean('Dry run', readonly=True)
    result = fields.Selection(RESULTS, 'Result', readonly=True, index=True)
    reason = fields.Char('Reason', readonly=True)
    duration_ms = fields.Integer('Duration (ms)', readonly=True, group_operator='avg')
    client_name = fields.Char('Client software', readonly=True)
    client_version = fields.Char('Client version', readonly=True)
    remote_addr = fields.Char('IP address', readonly=True)
    protocol_version = fields.Char('Protocol', readonly=True)
    create_date = fields.Datetime('Time', readonly=True, index=True)

    @api.model_cr
    def init(self):
        self.env.cr.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'kaitech_mcp_call_key_date_idx'")
        if not self.env.cr.fetchone():
            self.env.cr.execute("CREATE INDEX kaitech_mcp_call_key_date_idx ON kaitech_mcp_call (key_id, create_date)")

    @api.model
    def _purge_old_calls(self):
        days = grants.read_settings(self.env.cr)['audit_retention_days']
        if days <= 0:
            return 0
        self.env.cr.execute(
            "DELETE FROM kaitech_mcp_call WHERE create_date < (now() AT TIME ZONE 'UTC') - %s * interval '1 day'", (days,))
        removed = self.env.cr.rowcount
        _logger.info("MCP audit retention removed %s calls older than %s days", removed, days)
        return removed
