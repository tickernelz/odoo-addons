import json

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError

from .. import grants
from ..models.mcp_key import CALL_HELP, EXPLORE_HELP
from ..models.mcp_permission import MODEL_DOMAIN, MODES, access_warning

STEPS = [('client', 'Client and user'), ('models', 'Models and actions'), ('done', 'Copy the key')]


class McpKeyWizard(models.TransientModel):
    _name = 'kaitech.mcp.key.wizard'
    _description = 'New MCP Key'
    _transient_max_hours = 0.25

    step = fields.Selection(STEPS, default='client', required=True)
    name = fields.Char('Client', help="Who uses this key, for example 'Finance - Claude Desktop'.")
    user_id = fields.Many2one(
        'res.users', 'Acts as user', domain=[('id', '!=', SUPERUSER_ID), ('share', '=', False)],
        help="Every call runs as this Odoo user, with all of its access rights and record rules.")
    expires_at = fields.Datetime('Expires on', default=lambda self: self.env['kaitech.mcp.key']._default_expiry())
    line_ids = fields.One2many('kaitech.mcp.key.wizard.line', 'wizard_id', 'Models')
    allow_explore = fields.Boolean('Explore code', help=EXPLORE_HELP)
    allow_call = fields.Boolean('Call methods', help=CALL_HELP)
    key_id = fields.Many2one('kaitech.mcp.key', 'Key', readonly=True)
    token = fields.Char('Key', readonly=True)
    endpoint_url = fields.Char('Endpoint', compute='_compute_examples')
    config_desktop = fields.Text('Claude Desktop (mcp-remote)', compute='_compute_examples')
    config_http = fields.Text('Clients with HTTP headers (Cursor, n8n, Hermes)', compute='_compute_examples')
    claude_header = fields.Char('Header name for Claude', compute='_compute_examples')
    claude_value = fields.Char('Header value for Claude', compute='_compute_examples')
    delete_enabled = fields.Boolean(compute='_compute_delete_enabled')

    def _compute_delete_enabled(self):
        enabled = grants.current_settings(self.env)['delete_enabled']
        for wizard in self:
            wizard.delete_enabled = enabled

    @api.depends('token')
    def _compute_examples(self):
        url = self.env['kaitech.mcp.overview'].endpoint_url_for(self.env.cr.dbname)
        server = 'odoo-%s' % self.env.cr.dbname
        for wizard in self:
            wizard.endpoint_url = url
            if not wizard.token:
                wizard.config_desktop = wizard.config_http = False
                wizard.claude_header = wizard.claude_value = False
                continue
            bearer = 'Bearer %s' % wizard.token
            wizard.config_desktop = json.dumps({'mcpServers': {server: {
                'command': 'npx',
                'args': ['-y', 'mcp-remote', url, '--header', 'Authorization:${MCP_AUTH}'],
                'env': {'MCP_AUTH': bearer}}}}, indent=2)
            wizard.config_http = json.dumps({'mcpServers': {server: {
                'type': 'http', 'url': url, 'headers': {'Authorization': bearer}}}}, indent=2)
            wizard.claude_header = 'x-api-key'
            wizard.claude_value = wizard.token

    def _reopen(self, title=None):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'new',
            'name': title or self.env._('New MCP key'),
        }

    def action_next(self):
        self.ensure_one()
        if not self.name or not self.user_id:
            raise UserError(self.env._("Name the client and choose the Odoo user the key acts as."))
        draft = self.env['kaitech.mcp.key'].new({'name': self.name, 'user_id': self.user_id.id})
        draft._check_user()
        draft._check_within_your_groups()
        self.step = 'models'
        return self._reopen(self.env._('New MCP key: models and actions'))

    def action_back(self):
        self.ensure_one()
        self.step = 'client'
        return self._reopen()

    def action_create(self):
        self.ensure_one()
        if self.key_id:
            raise UserError(self.env._("This key was already created."))
        lines = self.line_ids.filtered(lambda line: line.can_read or line.can_create or line.can_update or line.can_delete
                                       or (line.can_call and self.allow_call))
        key = self.env['kaitech.mcp.key'].create({
            'name': self.name,
            'user_id': self.user_id.id,
            'expires_at': self.expires_at,
            'allow_explore': self.allow_explore,
            'allow_call': self.allow_call,
            'permission_ids': [fields.Command.create({
                'model_id': line.model_id.id, 'can_read': line.can_read, 'can_create': line.can_create,
                'can_update': line.can_update, 'can_delete': line.can_delete,
                'can_call': line.can_call and self.allow_call,
                'field_ids': [fields.Command.set(line.field_ids.ids)]}) for line in lines],
        })
        self.write({'key_id': key.id, 'token': key._issue_token(), 'step': 'done'})
        return self._reopen(self.env._('New MCP key: copy the key'))

    def action_done(self):
        """Forget the shown key and open the key it belongs to."""
        self.ensure_one()
        key = self.key_id
        self.write({'token': False})
        return {
            'type': 'ir.actions.act_window', 'res_model': 'kaitech.mcp.key', 'res_id': key.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current', 'name': key.name,
        }


class McpKeyWizardLine(models.TransientModel):
    _name = 'kaitech.mcp.key.wizard.line'
    _description = 'New MCP Key Model'

    wizard_id = fields.Many2one('kaitech.mcp.key.wizard', required=True, ondelete='cascade')
    model_id = fields.Many2one('ir.model', 'Model', required=True, domain=MODEL_DOMAIN)
    can_read = fields.Boolean('Read', default=True)
    can_create = fields.Boolean('Create')
    can_update = fields.Boolean('Update')
    can_delete = fields.Boolean('Delete')
    can_call = fields.Boolean('Call')
    field_ids = fields.Many2many(
        'ir.model.fields', 'kaitech_mcp_wizard_line_field_rel', 'line_id', 'field_id', 'Only these fields',
        domain="[('model_id', '=', model_id), ('ttype', '!=', 'binary')]")
    delete_enabled = fields.Boolean(related='wizard_id.delete_enabled')
    access_warning = fields.Char('Odoo access', compute='_compute_access_warning')

    @api.depends('model_id', 'wizard_id.user_id', 'can_read', 'can_create', 'can_update', 'can_delete')
    def _compute_access_warning(self):
        for line in self:
            flags = [(line._fields[flag].string, mode, line[flag]) for flag, mode in MODES]
            line.access_warning = access_warning(self.env, line.wizard_id.user_id, line.model_id.model, flags)
