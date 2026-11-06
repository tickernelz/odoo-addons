# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .. import grants

PARAMS = (
    'delete_enabled', 'sql_enabled', 'sql_statement_timeout_ms', 'extra_forbidden_tables', 'audit_retention_days', 'default_expiry_days', 'default_rate_per_minute',
    'default_writes_per_minute', 'default_max_read', 'default_max_write', 'default_max_delete',
    'extra_forbidden_models', 'extra_forbidden_fields',
)


class McpSettings(models.TransientModel):
    _name = 'kaitech.mcp.settings'
    _inherit = 'res.config.settings'
    _description = 'MCP Settings'

    delete_enabled = fields.Boolean(
        'Allow deleting through MCP',
        help="Off by default. When off, no key can delete, whatever its permissions say.")
    sql_enabled = fields.Boolean(
        'Allow SQL through MCP',
        help="Off by default. SQL bypasses Odoo access rights, record rules, constraints, computed fields and "
             "tracking. When off, no key can use the sql_ tools, whatever its own settings say.")
    sql_statement_timeout_ms = fields.Integer('SELECT query time limit (ms)')
    extra_forbidden_tables = fields.Text(
        'Also forbidden tables',
        help="One per line or comma separated; wildcards allowed, for example hr_payslip* or account_*")
    audit_retention_days = fields.Integer('Keep call log (days)', help="0 keeps the call log forever.")
    default_expiry_days = fields.Integer('New keys expire after (days)', help="0 creates keys that never expire.")
    default_rate_per_minute = fields.Integer('Calls per minute')
    default_writes_per_minute = fields.Integer('Writes per minute')
    default_max_read = fields.Integer('Records per read')
    default_max_write = fields.Integer('Records per update')
    default_max_delete = fields.Integer('Records per delete')
    extra_forbidden_models = fields.Text(
        'Also forbidden models',
        help="One per line or comma separated; wildcards allowed, for example hr.payslip* or account.*")
    extra_forbidden_fields = fields.Text(
        'Also forbidden fields',
        help="One per line or comma separated: a field name (every model) or model.field, for example res.partner.vat")

    def _check_settings_access(self):
        if not (self.env.user._is_superuser() or self.env.user.has_group('base.group_system')):
            raise AccessError(_("Only administrators can read or change the MCP settings."))

    @api.model
    def get_default_mcp_values(self, fields_list):
        self._check_settings_access()
        settings = grants.read_settings(self.env.cr)
        return dict((name, settings[name] or (False if isinstance(settings[name], basestring) else settings[name]))
                    for name in PARAMS)

    @api.multi
    def set_mcp_values(self):
        self.ensure_one()
        self._check_settings_access()
        for name in ('default_rate_per_minute', 'default_writes_per_minute', 'default_max_read',
                     'default_max_write', 'default_max_delete'):
            if self[name] <= 0:
                raise ValidationError(_("Default limits must be greater than zero."))
        if self.default_max_delete > grants.HARD_MAX_DELETE:
            raise ValidationError(_("At most %s records can be deleted per call.") % grants.HARD_MAX_DELETE)
        if self.sql_statement_timeout_ms < 100 or self.sql_statement_timeout_ms > 60000:
            raise ValidationError(_("The SELECT query time limit must be between 100 and 60000 ms."))
        if self.audit_retention_days < 0 or self.default_expiry_days < 0:
            raise ValidationError(_("Days cannot be negative."))
        parameters = self.env['ir.config_parameter'].sudo()
        for name in PARAMS:
            kind = self._fields[name].type
            value = self[name]
            if kind == 'boolean':
                stored = 'True' if value else 'False'
            elif kind == 'integer':
                stored = '%d' % value
            else:
                stored = value or ''
            parameters.set_param(grants.PARAM_PREFIX + name, stored)
        if not self.delete_enabled:
            self.env['kaitech.mcp.permission'].sudo().search([('can_delete', '=', True)]).write({'can_delete': False})
            self.env['kaitech.mcp.sql.table'].sudo().search([('can_delete', '=', True)]).write({'can_delete': False})
        if not self.sql_enabled:
            self.env['kaitech.mcp.key'].sudo().with_context(active_test=False).search(
                [('allow_sql', '=', True)]).write({'allow_sql': False})
