from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .. import grants

LIMITS = ('default_rate_per_minute', 'default_writes_per_minute', 'default_max_read', 'default_max_write',
          'default_max_delete', 'sql_statement_timeout_ms')


class McpSettings(models.TransientModel):
    _name = 'kaitech.mcp.settings'
    _description = 'MCP Settings'

    delete_enabled = fields.Boolean(
        'Allow deleting through MCP',
        help="Off by default. When off, no key can delete, whatever its permissions say.")
    sql_enabled = fields.Boolean(
        'Allow SQL through MCP',
        help="Off by default. SQL bypasses Odoo access rights, record rules, Python constraints, computed fields "
             "and tracking. When off, no key sees the SQL tools, whatever its SQL tables say.")
    sql_statement_timeout_ms = fields.Integer('SQL time limit (ms)')
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
    extra_forbidden_tables = fields.Text(
        'Also forbidden tables',
        help="One per line or comma separated; wildcards allowed, for example hr_payslip*")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        settings = grants.current_settings(self.env)
        for name in fields_list:
            if name in grants.DEFAULTS:
                value = settings[name]
                values[name] = value if value != '' else False
        return values

    def action_save(self):
        self.ensure_one()
        for name in LIMITS:
            if self[name] <= 0:
                raise ValidationError(self.env._("Default limits must be greater than zero."))
        if self.default_max_delete > grants.HARD_MAX_DELETE:
            raise ValidationError(self.env._("At most %s records can be deleted per call.", grants.HARD_MAX_DELETE))
        if self.audit_retention_days < 0 or self.default_expiry_days < 0:
            raise ValidationError(self.env._("Days cannot be negative."))
        parameters = self.env['ir.config_parameter'].sudo()
        for name in grants.DEFAULTS:
            value = self[name]
            if isinstance(grants.DEFAULTS[name], bool):
                text = 'True' if value else 'False'
            elif isinstance(grants.DEFAULTS[name], int):
                text = '%d' % (value or 0)
            else:
                text = value or ''
            parameters.set_param(grants.PARAM_PREFIX + name, text)
        if not self.delete_enabled:
            self.env['kaitech.mcp.permission'].sudo().search([('can_delete', '=', True)]).write({'can_delete': False})
            self.env['kaitech.mcp.sql.table'].sudo().search([('can_delete', '=', True)]).write({'can_delete': False})
        if not self.sql_enabled:
            self.env['kaitech.mcp.key'].sudo().search([('allow_sql', '=', True)]).write({'allow_sql': False})
        return {'type': 'ir.actions.client', 'tag': 'reload'}
