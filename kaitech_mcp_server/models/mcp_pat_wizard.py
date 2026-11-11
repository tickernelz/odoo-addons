from odoo import fields, models


class McpPatWizard(models.TransientModel):
    """One-time display wizard for a newly generated Personal Access Token.

    The raw token is stored only in this transient record and never persisted
    anywhere else. Once the user closes this dialog, the raw token is gone.
    """

    _name = "mcp.pat.wizard"
    _description = "MCP PAT — One-Time Display"

    token_id = fields.Many2one("mcp.token", readonly=True, ondelete="cascade")
    raw_token = fields.Char(string="Personal Access Token", readonly=True)
    user_id = fields.Many2one(related="token_id.user_id", string="User", readonly=True)
    expires_at = fields.Datetime(related="token_id.expires_at", string="Expires At", readonly=True)
