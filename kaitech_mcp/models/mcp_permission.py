from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .. import grants

MODES = (('can_read', 'read'), ('can_create', 'create'), ('can_update', 'write'), ('can_delete', 'unlink'))
MODEL_DOMAIN = [('transient', '=', False), '!', ('model', '=like', 'ir.%'), '!', ('model', '=like', 'base.%'),
                '!', ('model', '=like', 'bus.%'), '!', ('model', '=like', 'kaitech.mcp.%'),
                '!', ('model', '=like', 'res.users%'), '!', ('model', '=like', 'res.groups%'),
                '!', ('model', '=like', 'res.device%'), '!', ('model', '=like', 'auth_totp.%')]


def access_warning(env, user, model_name, flags):
    """Text naming the actions the Odoo user itself cannot do on model_name, or False."""
    if not user or not model_name or model_name not in env:
        return False
    model = env[model_name].with_user(user)
    missing = [label for label, mode, wanted in flags if wanted and not model.has_access(mode)]
    return missing and env._("%(user)s cannot: %(actions)s", user=user.name, actions=', '.join(missing)) or False


class McpPermission(models.Model):
    _name = 'kaitech.mcp.permission'
    _description = 'MCP Key Model Permission'
    _order = 'model_name'

    key_id = fields.Many2one('kaitech.mcp.key', 'Key', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', 'Model', required=True, ondelete='cascade', domain=MODEL_DOMAIN)
    model_name = fields.Char('Technical name', related='model_id.model', store=True, readonly=True)
    can_read = fields.Boolean('Read', default=True)
    can_create = fields.Boolean('Create')
    can_update = fields.Boolean('Update')
    can_delete = fields.Boolean('Delete')
    can_call = fields.Boolean('Call', help="Public business methods of this model may be called when the key allows method calls.")
    field_ids = fields.Many2many(
        'ir.model.fields', 'kaitech_mcp_permission_field_rel', 'permission_id', 'field_id', 'Only these fields',
        domain="[('model_id', '=', model_id), ('ttype', '!=', 'binary')]",
        help="Leave empty to allow every field that is not forbidden.")
    delete_enabled = fields.Boolean('Delete allowed in settings', compute='_compute_delete_enabled')
    access_warning = fields.Char('Odoo access', compute='_compute_access_warning')

    _key_model_unique = models.Constraint('UNIQUE(key_id, model_id)', 'Each model can be listed only once per key.')

    def _compute_delete_enabled(self):
        enabled = grants.current_settings(self.env)['delete_enabled']
        for row in self:
            row.delete_enabled = enabled

    @api.depends('model_id', 'key_id.user_id', 'can_read', 'can_create', 'can_update', 'can_delete')
    def _compute_access_warning(self):
        for row in self:
            flags = [(row._fields[flag].string, mode, row[flag]) for flag, mode in MODES]
            row.access_warning = access_warning(self.env, row.key_id.user_id, row.model_id.model, flags)

    @api.constrains('model_id')
    def _check_model(self):
        settings = grants.current_settings(self.env)
        patterns = grants.forbidden_model_patterns(settings)
        for row in self:
            name = row.model_id.model
            if grants.model_is_forbidden(name, patterns) or not grants.modelable(self.env, name):
                raise ValidationError(self.env._("Model %s can never be used through MCP.", name))

    @api.constrains('can_delete')
    def _check_delete(self):
        if any(row.can_delete for row in self) and not grants.current_settings(self.env)['delete_enabled']:
            raise ValidationError(self.env._("Deleting through MCP is switched off in MCP Settings."))

    @api.constrains('field_ids', 'model_id')
    def _check_fields(self):
        for row in self:
            if row.field_ids.filtered(lambda f: f.model_id != row.model_id or f.ttype == 'binary'):
                raise ValidationError(self.env._("Allowed fields must belong to the model and cannot be binary."))
