from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_kai_timezone = fields.Selection(related='pos_config_id.kai_timezone', readonly=False)
