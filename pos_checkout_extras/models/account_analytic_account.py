from odoo import Command, api, fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"

    pos_config_ids = fields.Many2many(
        "pos.config",
        "pos_config_analytic_account_rel",
        "account_id",
        "config_id",
        string="Points of Sale",
        copy=False,
        check_company=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        configs = self.env["pos.config"].union(*(
            self._pos_configs_named(vals["pos_config_ids"]) for vals in vals_list if vals.get("pos_config_ids")
        ))
        old_distributions = {config.id: config.analytic_distribution for config in configs}
        accounts = super().create(vals_list)
        configs._settle_account_change(old_distributions)
        return accounts

    def write(self, vals):
        if "pos_config_ids" not in vals:
            return super().write(vals)
        configs = self.pos_config_ids | self._pos_configs_named(vals["pos_config_ids"])
        old_distributions = {config.id: config.analytic_distribution for config in configs}
        result = super().write(vals)
        configs._settle_account_change(old_distributions)
        return result

    def unlink(self):
        configs = self.pos_config_ids
        old_distributions = {config.id: config.analytic_distribution for config in configs}
        result = super().unlink()
        configs = configs.exists()
        configs.invalidate_recordset(["analytic_account_ids"])
        self.env.add_to_compute(configs._fields["analytic_distribution"], configs)
        configs._settle_account_change(old_distributions)
        return result

    def _pos_configs_named(self, value):
        if isinstance(value, models.BaseModel):
            value = [Command.set(value._origin.ids)]
        elif isinstance(value, tuple) or (isinstance(value, list) and value and not isinstance(value[0], (tuple, list))):
            value = [Command.set(list(value))]
        ids = set()
        for command in value or ():
            if command[0] in (Command.LINK, Command.UNLINK):
                ids.add(command[1])
            elif command[0] == Command.SET:
                ids.update(command[2])
        return self.env["pos.config"].browse(ids).exists()
