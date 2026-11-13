from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_customer_required_mode = fields.Selection(related="pos_config_id.customer_required_mode", readonly=False)
    pos_allow_payment_reference = fields.Boolean(related="pos_config_id.allow_payment_reference", readonly=False)
    pos_analytic_distribution = fields.Json(related="pos_config_id.analytic_distribution", readonly=False)
    pos_analytic_account_ids = fields.Many2many(related="pos_config_id.analytic_account_ids", readonly=False)
    pos_analytic_scope = fields.Selection(related="pos_config_id.analytic_scope", readonly=False)
    analytic_precision = fields.Integer(related="pos_config_id.analytic_precision")

    @api.onchange("pos_analytic_account_ids")
    def _onchange_pos_analytic_account_ids(self):
        configs = self.env["pos.config"]
        for settings in self:
            settings.pos_analytic_distribution = configs._distribution_for_accounts(
                settings.pos_analytic_account_ids, settings.pos_analytic_distribution)

    @api.onchange("pos_analytic_distribution")
    def _onchange_pos_analytic_distribution(self):
        for settings in self:
            settings.pos_analytic_account_ids = self.env["pos.config"]._distribution_accounts(
                settings.pos_analytic_distribution)

    def action_pos_analytic_sync_current(self):
        return self.env["pos.analytic.sync.wizard"].action_open(self.pos_config_id)

    def action_pos_analytic_sync_all(self):
        return self.env["pos.analytic.sync.wizard"].action_open(self.env["pos.config"])
