from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_customer_required_mode = fields.Selection(related="pos_config_id.customer_required_mode", readonly=False)
    pos_allow_payment_reference = fields.Boolean(related="pos_config_id.allow_payment_reference", readonly=False)
    pos_analytic_distribution = fields.Json(related="pos_config_id.analytic_distribution", readonly=False)
    pos_analytic_scope = fields.Selection(related="pos_config_id.analytic_scope", readonly=False)
    analytic_precision = fields.Integer(related="pos_config_id.analytic_precision")

    def action_pos_analytic_sync_current(self):
        return self.env["pos.analytic.sync.wizard"].action_open(self.pos_config_id)

    def action_pos_analytic_sync_all(self):
        return self.env["pos.analytic.sync.wizard"].action_open(self.env["pos.config"])
