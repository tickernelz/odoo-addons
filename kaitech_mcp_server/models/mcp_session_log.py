from odoo import models, fields


class McpSessionLog(models.Model):
    _name = 'mcp.session.log'
    _description = 'MCP Tool Call Log'
    _order = 'create_date desc'
    _rec_name = 'tool_name'

    session_id = fields.Many2one(
        'mcp.session', string='Session',
        ondelete='cascade', index=True, readonly=True,
    )
    user_id = fields.Many2one('res.users', string='User', readonly=True, ondelete='set null')
    tool_name = fields.Char(string='Tool', readonly=True, index=True)
    odoo_model = fields.Char(string='Model', readonly=True)
    record_count = fields.Integer(string='Records', readonly=True, default=0)
    duration_ms = fields.Integer(string='ms', readonly=True, default=0)
    is_error = fields.Boolean(string='Error', readonly=True, default=False)
    error_message = fields.Char(string='Error Detail', readonly=True)
    transport = fields.Selection(
        [('sse', 'SSE'), ('http', 'HTTP')],
        string='Transport', readonly=True,
    )

    # Feature 4: payload capture fields — populated only when token.capture_payloads=True
    request_payload = fields.Text(
        string='Request Payload',
        readonly=True,
        help='JSON-encoded tool arguments (PII redacted, truncated to 4000 chars). '
             'Only captured when the token has "Capture Payloads" enabled.',
    )
    response_payload = fields.Text(
        string='Response Payload',
        readonly=True,
        help='JSON-encoded tool result (PII redacted, truncated to 4000 chars). '
             'Only captured when the token has "Capture Payloads" enabled.',
    )
