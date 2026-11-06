from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .. import grants, sql
from .mcp_permission import MODEL_DOMAIN


class McpSqlTable(models.Model):
    _name = 'kaitech.mcp.sql.table'
    _description = 'MCP Key SQL Table'
    _order = 'table_name'
    _rec_name = 'table_name'

    key_id = fields.Many2one('kaitech.mcp.key', 'Key', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one(
        'ir.model', 'From model', domain=MODEL_DOMAIN,
        help="Pick a model to fill in its table, or type the table name directly.")
    table_name = fields.Char('Table', required=True, help="Database table in the public schema, for example res_partner.")
    can_select = fields.Boolean('Select', default=True)
    can_insert = fields.Boolean('Insert')
    can_update = fields.Boolean('Update')
    can_delete = fields.Boolean('Delete')
    delete_enabled = fields.Boolean('Delete allowed in settings', compute='_compute_delete_enabled')

    _key_table_unique = models.Constraint('UNIQUE(key_id, table_name)', 'Each table can be listed only once per key.')

    @api.onchange('model_id')
    def _onchange_model_id(self):
        for row in self:
            if row.model_id and row.model_id.model in self.env:
                row.table_name = self.env[row.model_id.model]._table

    def _compute_delete_enabled(self):
        enabled = grants.current_settings(self.env)['delete_enabled']
        for row in self:
            row.delete_enabled = enabled

    @api.constrains('table_name')
    def _check_table(self):
        settings = grants.current_settings(self.env)
        existing = set(sql.database_tables(self.env.cr))
        for row in self:
            if sql.table_is_forbidden(row.table_name, settings):
                raise ValidationError(self.env._("Table %s can never be used through MCP.", row.table_name))
            if row.table_name not in existing:
                raise ValidationError(self.env._("There is no table %s in this database.", row.table_name))

    @api.constrains('can_delete')
    def _check_delete(self):
        if any(row.can_delete for row in self) and not grants.current_settings(self.env)['delete_enabled']:
            raise ValidationError(self.env._("Deleting through MCP is switched off in MCP Settings."))
