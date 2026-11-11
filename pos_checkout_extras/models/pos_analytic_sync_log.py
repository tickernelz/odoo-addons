from odoo import fields, models


class PosAnalyticSyncLog(models.Model):
    _name = "pos.analytic.sync.log"
    _description = "POS Analytic Sync Log"
    _order = "id desc"

    config_id = fields.Many2one("pos.config", string="Point of Sale", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="config_id.company_id", store=True)
    kind = fields.Selection(
        [("distribution", "Distribution changed"), ("sync", "Synchronisation")],
        required=True,
    )
    user_id = fields.Many2one("res.users", string="User", default=lambda self: self.env.user, required=True)
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    mode = fields.Selection(
        [("missing", "Items without a distribution"), ("replace", "Every item")],
    )
    updated_count = fields.Integer(string="Updated")
    skipped_count = fields.Integer(string="Skipped")
    old_distribution = fields.Json()
    new_distribution = fields.Json()
