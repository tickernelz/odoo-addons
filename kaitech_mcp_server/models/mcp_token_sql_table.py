from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import grants, sql


class McpTokenSqlTable(models.Model):
    _name = "mcp.token.sql.table"
    _description = "MCP Token SQL Table"
    _order = "name"

    token_id = fields.Many2one("mcp.token", string="Token", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="From Model", domain=[("transient", "=", False)],
        help="Pick a model to fill in its table, or type the table name directly.",
    )
    name = fields.Char(string="Table", required=True,
                       help="Database table in the public schema, for example res_partner.")
    can_select = fields.Boolean(string="Select", default=True)
    can_insert = fields.Boolean(string="Insert")
    can_update = fields.Boolean(string="Update")
    can_delete = fields.Boolean(string="Delete")

    _token_table_unique = models.Constraint(
        "UNIQUE(token_id, name)", "Each table can be listed only once per token.",
    )

    @api.onchange("model_id")
    def _onchange_model_id(self):
        for row in self:
            if row.model_id and row.model_id.model in self.env:
                row.name = self.env[row.model_id.model]._table

    @api.constrains("name")
    def _check_table(self):
        settings = grants.read_settings(self.env)
        existing = set(sql.database_tables(self.env.cr))
        for row in self:
            if sql.table_is_forbidden(row.name, settings):
                raise ValidationError(self.env._("Table %s can never be used through MCP.", row.name))
            if row.name not in existing:
                raise ValidationError(self.env._("There is no table %s in this database.", row.name))

    @api.constrains("can_delete")
    def _check_delete(self):
        if any(row.can_delete for row in self) and not grants.read_settings(self.env)["delete_enabled"]:
            raise ValidationError(self.env._("Deleting through MCP is switched off in MCP Server settings."))

    def _copy_values(self):
        self.ensure_one()
        return {
            "model_id": self.model_id.id,
            "name": self.name,
            "can_select": self.can_select,
            "can_insert": self.can_insert,
            "can_update": self.can_update,
            "can_delete": self.can_delete,
        }
