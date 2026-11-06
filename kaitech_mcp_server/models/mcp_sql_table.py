# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .. import grants, sql


class McpSqlTable(models.Model):
    _name = 'kaitech.mcp.sql.table'
    _description = 'MCP Key SQL Table'
    _order = 'table_name'
    _rec_name = 'table_name'

    key_id = fields.Many2one('kaitech.mcp.key', 'Key', required=True, ondelete='cascade', index=True)
    table_name = fields.Selection('_table_selection', 'Table', required=True)
    can_select = fields.Boolean('Select', default=True)
    can_insert = fields.Boolean('Insert')
    can_update = fields.Boolean('Update')
    can_delete = fields.Boolean('Delete')
    delete_enabled = fields.Boolean('Delete allowed in settings', compute='_compute_delete_enabled')

    _sql_constraints = [
        ('key_table_unique', 'UNIQUE(key_id, table_name)', 'Each table can be listed only once per key.'),
    ]

    @api.model
    def _table_selection(self):
        settings = grants.read_settings(self.env.cr)
        return [(name, name) for name in sql.database_tables(self.env.cr) if not sql.table_is_forbidden(name, settings)]

    def _compute_delete_enabled(self):
        enabled = grants.read_settings(self.env.cr)['delete_enabled']
        for row in self:
            row.delete_enabled = enabled

    @api.constrains('table_name')
    def _check_table(self):
        settings = grants.read_settings(self.env.cr)
        for row in self:
            if sql.table_is_forbidden(row.table_name, settings):
                raise ValidationError(_("Table %s can never be used through MCP.") % row.table_name)

    @api.constrains('can_delete')
    def _check_delete(self):
        if any(row.can_delete for row in self) and not grants.read_settings(self.env.cr)['delete_enabled']:
            raise ValidationError(_("Deleting through MCP is switched off in MCP Settings."))
