# -*- coding: utf-8 -*-
import json

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

from .. import grants
from ..models.mcp_key import check_manager, check_within_your_groups

STEPS = [('client', 'Client and user'), ('models', 'Models and actions'), ('done', 'Copy the key')]


class McpKeyWizard(models.TransientModel):
    _name = 'kaitech.mcp.key.wizard'
    _description = 'New MCP Key'

    step = fields.Selection(STEPS, default='client', required=True)
    name = fields.Char('Client', help="Who uses this key, for example 'Alice - Claude Desktop'.")
    user_id = fields.Many2one(
        'res.users', 'Acts as user', domain=[('id', '!=', SUPERUSER_ID), ('share', '=', False)],
        help="Every call runs as this Odoo user, with all of its access rights and record rules.")
    expires_at = fields.Datetime('Expires on', default=lambda self: self.env['kaitech.mcp.key']._default_expiry())
    line_ids = fields.One2many('kaitech.mcp.key.wizard.line', 'wizard_id', 'Models')
    allow_explore = fields.Boolean(
        'Explore code', help="The client may read the structure and source code of the installed modules.")
    allow_methods = fields.Boolean(
        'Call methods', help="The client may call public business methods on the models ticked Call methods.")
    key_id = fields.Many2one('kaitech.mcp.key', 'Key', readonly=True)
    token = fields.Char('Key', readonly=True)
    endpoint_url = fields.Char('Endpoint', compute='_compute_examples')
    config_desktop = fields.Text('Claude Desktop (mcp-remote)', compute='_compute_examples')
    config_http = fields.Text('Clients with HTTP headers (Cursor, n8n, Hermes)', compute='_compute_examples')
    delete_enabled = fields.Boolean(compute='_compute_delete_enabled')

    @api.model
    def create(self, values):
        check_manager(self.env)
        return super(McpKeyWizard, self).create(values)

    def _compute_delete_enabled(self):
        enabled = grants.read_settings(self.env.cr)['delete_enabled']
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
                continue
            bearer = 'Bearer %s' % wizard.token
            wizard.config_desktop = json.dumps({'mcpServers': {server: {
                'command': 'npx',
                'args': ['-y', 'mcp-remote', url, '--header', 'Authorization:${MCP_AUTH}'],
                'env': {'MCP_AUTH': bearer}}}}, indent=2)
            wizard.config_http = json.dumps({'mcpServers': {server: {
                'type': 'http', 'url': url, 'headers': {'Authorization': bearer}}}}, indent=2)

    @api.multi
    def _reopen(self, title=None):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new', 'name': title or _('New MCP key'),
        }

    @api.multi
    def action_next(self):
        self.ensure_one()
        if not self.name or not self.user_id:
            raise UserError(_("Name the client and choose the Odoo user the key acts as."))
        if self.user_id.id == SUPERUSER_ID:
            raise UserError(_(
                "An MCP key cannot act as the Administrator (uid 1): that user bypasses every access right "
                "and record rule. Create a dedicated user for this client with only the groups it needs."))
        check_within_your_groups(self.env, self.user_id)
        self.step = 'models'
        return self._reopen(_('New MCP key: models and actions'))

    @api.multi
    def action_back(self):
        self.ensure_one()
        self.step = 'client'
        return self._reopen()

    @api.multi
    def action_create(self):
        self.ensure_one()
        if self.key_id:
            raise UserError(_("This key was already created."))
        lines = self.line_ids.filtered(lambda line: line.can_read or line.can_create or line.can_update
                                       or line.can_delete or line.can_call)
        key = self.env['kaitech.mcp.key'].create({
            'name': self.name,
            'user_id': self.user_id.id,
            'expires_at': self.expires_at,
            'allow_explore': self.allow_explore,
            'allow_methods': self.allow_methods,
            'permission_ids': [(0, 0, {
                'model_id': line.model_id.id, 'can_read': line.can_read, 'can_create': line.can_create,
                'can_update': line.can_update, 'can_delete': line.can_delete, 'can_call': line.can_call,
                'field_ids': [(6, 0, line.field_ids.ids)]}) for line in lines],
        })
        self.write({'key_id': key.id, 'token': key._issue_token(), 'step': 'done'})
        return self._reopen(_('New MCP key: copy the key'))

    @api.multi
    def action_done(self):
        """Forget the shown key and open the key it belongs to."""
        self.ensure_one()
        key = self.key_id
        self.write({'token': False})
        return {
            'type': 'ir.actions.act_window', 'res_model': 'kaitech.mcp.key', 'res_id': key.id,
            'view_mode': 'form', 'target': 'current', 'name': key.name,
        }


class McpKeyWizardLine(models.TransientModel):
    _name = 'kaitech.mcp.key.wizard.line'
    _description = 'New MCP Key Model'

    wizard_id = fields.Many2one('kaitech.mcp.key.wizard', required=True, ondelete='cascade')
    model_id = fields.Many2one(
        'ir.model', 'Model', required=True,
        domain=[('transient', '=', False), '!', ('model', '=like', 'ir.%'), '!', ('model', '=like', 'base.%'),
                '!', ('model', '=like', 'bus.%'), '!', ('model', '=like', 'kaitech.mcp.%'),
                '!', ('model', '=like', 'res.users%'), '!', ('model', '=like', 'res.groups%')])
    can_read = fields.Boolean('Read', default=True)
    can_create = fields.Boolean('Create')
    can_update = fields.Boolean('Update')
    can_delete = fields.Boolean('Delete')
    can_call = fields.Boolean('Call methods')
    methods_allowed = fields.Boolean(related='wizard_id.allow_methods')
    field_ids = fields.Many2many(
        'ir.model.fields', 'kaitech_mcp_wizard_line_field_rel', 'line_id', 'field_id', 'Only these fields',
        domain="[('model_id', '=', model_id), ('ttype', '!=', 'binary')]")
    delete_enabled = fields.Boolean(related='wizard_id.delete_enabled')
    access_warning = fields.Char('Odoo access', compute='_compute_access_warning')

    @api.depends('model_id', 'wizard_id.user_id', 'can_read', 'can_create', 'can_update', 'can_delete')
    def _compute_access_warning(self):
        access = self.env['ir.model.access']
        modes = (('can_read', 'read'), ('can_create', 'create'), ('can_update', 'write'), ('can_delete', 'unlink'))
        for line in self:
            user = line.wizard_id.user_id
            if not line.model_id or not user:
                line.access_warning = False
                continue
            missing = [line._fields[flag].string for flag, mode in modes
                       if line[flag] and not access.sudo(user.id).check(line.model_id.model, mode, raise_exception=False)]
            line.access_warning = missing and _("%s cannot: %s") % (user.name, ', '.join(missing)) or False
