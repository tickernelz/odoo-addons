from odoo import fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"

    pos_config_ids = fields.Many2many(
        "pos.config",
        "pos_config_analytic_account_rel",
        "account_id",
        "config_id",
        string="Points of Sale",
    )
