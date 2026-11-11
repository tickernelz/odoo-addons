from datetime import timedelta

from odoo import api, models, fields


class McpSession(models.Model):
    _name = 'mcp.session'
    _description = 'MCP Session'
    _order = 'create_date desc'
    _rec_name = 'session_id'

    session_id = fields.Char(string='Session ID', readonly=True, index=True)
    user_id = fields.Many2one('res.users', string='User', readonly=True, ondelete='set null')
    token_id = fields.Many2one('mcp.token', string='Token', readonly=True, ondelete='set null')
    transport = fields.Selection(
        [('sse', 'SSE'), ('http', 'HTTP')],
        string='Transport', default='sse', readonly=True,
    )
    state = fields.Selection([
        ('active', 'Active'),
        ('closed', 'Closed'),
    ], string='State', default='active', readonly=True)
    ip_address = fields.Char(string='IP Address', readonly=True)
    message_count = fields.Integer(string='Messages', default=0, readonly=True)
    last_activity = fields.Datetime(string='Last Activity', readonly=True)
    log_ids = fields.One2many('mcp.session.log', 'session_id', string='Tool Calls', readonly=True)
    tool_call_count = fields.Integer(
        string='Tool Calls', compute='_compute_tool_call_count', store=True,
    )

    @api.depends('log_ids')
    def _compute_tool_call_count(self):
        for rec in self:
            rec.tool_call_count = len(rec.log_ids)

    def action_close(self):
        self.write({'state': 'closed'})

    @api.model
    def _vacuum_old_sessions(self):
        """Delete closed or inactive sessions and tool call logs beyond configurable retention.

        Retention windows are read from ir.config_parameter so they can be tuned
        via Settings → MCP Server without touching code.
        """
        ICP = self.env["ir.config_parameter"].sudo()

        try:
            session_days = int(ICP.get_param("mcp_server.session_retention_days", 7))
        except (ValueError, TypeError):
            session_days = 7

        try:
            log_days = int(ICP.get_param("mcp_server.log_retention_days", 30))
        except (ValueError, TypeError):
            log_days = 30

        now = fields.Datetime.now()
        cutoff_session = now - timedelta(days=session_days)
        old_sessions = self.search([
            "|",
            ("state", "=", "closed"),
            ("create_date", "<", cutoff_session),
        ])
        old_sessions.unlink()

        # Logs on long-lived HTTP sessions accumulate indefinitely — prune separately.
        cutoff_log = now - timedelta(days=log_days)
        old_logs = self.env["mcp.session.log"].sudo().search([
            ("create_date", "<", cutoff_log),
        ])
        if old_logs:
            old_logs.unlink()
