from odoo import fields, models


class ResUsersMcp(models.Model):
    _inherit = "res.users"

    mcp_rate_limit = fields.Integer(
        string="MCP Rate Limit",
        default=0,
        help=(
            "Custom MCP requests-per-minute limit for this user. "
            "0 = use global default from odoo.conf (mcp_rate_limit)."
        ),
    )
