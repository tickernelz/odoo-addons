# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

from .. import grants, keys

STATES = [('active', 'Active'), ('expiring', 'Expiring soon'), ('expired', 'Expired'), ('revoked', 'Revoked')]


def _setting(env, name):
    return grants.read_settings(env.cr)[name]


def groups_beyond(env, user):
    """Groups of user that the current user lacks; empty for the superuser."""
    if env.user._is_superuser():
        return env['res.groups']
    return user.groups_id - env.user.groups_id


def check_within_your_groups(env, user):
    missing = groups_beyond(env, user)
    if missing:
        raise ValidationError(_(
            "You cannot create a key for %s: that user has groups you do not have (%s). "
            "Ask an administrator, or choose a user with fewer rights.") % (
            user.name, ', '.join(missing.mapped('full_name'))))


def check_manager(env):
    if not env.user.has_group('kaitech_mcp_server.group_mcp_manager'):
        raise AccessError(_("Only MCP managers can manage MCP keys."))


class McpKey(models.Model):
    _name = 'kaitech.mcp.key'
    _description = 'MCP Key'
    _order = 'revoked_at desc, name, id'

    name = fields.Char('Client', required=True, help="Who uses this key, for example 'Alice - Claude Desktop'.")
    user_id = fields.Many2one(
        'res.users', 'Acts as user', required=True, ondelete='restrict',
        domain=[('id', '!=', SUPERUSER_ID)],
        help="Every call runs as this Odoo user, with all of its access rights and record rules.")
    key_index = fields.Char('Key prefix', readonly=True, index=True, copy=False)
    key_hash = fields.Char(readonly=True, copy=False, groups='base.group_system')
    expires_at = fields.Datetime(
        'Expires on', copy=False,
        default=lambda self: self._default_expiry(),
        help="Leave empty for a key that never expires.")
    revoked_at = fields.Datetime('Revoked on', readonly=True, copy=False)
    revoked_by = fields.Many2one('res.users', 'Revoked by', readonly=True, copy=False)
    rotated_from_id = fields.Many2one('kaitech.mcp.key', 'Replaces', readonly=True, copy=False)
    rate_per_minute = fields.Integer('Calls per minute', default=lambda self: _setting(self.env, 'default_rate_per_minute'))
    writes_per_minute = fields.Integer('Writes per minute', default=lambda self: _setting(self.env, 'default_writes_per_minute'))
    max_read = fields.Integer('Records per read', default=lambda self: _setting(self.env, 'default_max_read'))
    max_write = fields.Integer('Records per update', default=lambda self: _setting(self.env, 'default_max_write'))
    max_delete = fields.Integer('Records per delete', default=lambda self: _setting(self.env, 'default_max_delete'))
    permission_ids = fields.One2many('kaitech.mcp.permission', 'key_id', 'Models', copy=True)
    allow_sql = fields.Boolean(
        'Allow SQL', copy=True,
        help="SQL runs as the database itself: it bypasses Odoo access rights, record rules, constraints, "
             "computed fields and tracking. Only the tables listed below can be used, and only when SQL "
             "is also switched on in MCP Settings.")
    sql_table_ids = fields.One2many('kaitech.mcp.sql.table', 'key_id', 'SQL tables', copy=True)
    allow_explore = fields.Boolean(
        'Explore code', copy=True,
        help="The client may read the structure and source code of the installed modules: models, fields, "
             "methods, views, menus and actions, and the source files in the addons folders. It never reads "
             "records this way, and nothing outside the addons folders.")
    allow_methods = fields.Boolean(
        'Call methods', copy=True,
        help="The client may call public business methods (for example action_confirm) on the models ticked "
             "Call methods on the Models tab, as the user of this key, so Odoo access rights and record rules still apply. "
             "Private methods and ORM methods such as write or unlink are always refused.")
    sql_enabled = fields.Boolean('SQL allowed in settings', compute='_compute_sql_enabled')
    call_ids = fields.One2many('kaitech.mcp.call', 'key_id', 'Calls', readonly=True)
    state = fields.Selection(STATES, 'Status', compute='_compute_state', search='_search_state')
    model_count = fields.Integer('Models and tables', compute='_compute_permission_summary')
    can_delete = fields.Boolean('Delete', compute='_compute_permission_summary')
    last_used_at = fields.Datetime('Last used', compute='_compute_usage')
    calls_24h = fields.Integer('Calls (24 h)', compute='_compute_usage')
    refused_24h = fields.Integer('Refused or limited (24 h)', compute='_compute_usage')
    endpoint_url = fields.Char('Endpoint', compute='_compute_endpoint_url')

    _sql_constraints = [
        ('positive_limits',
         'CHECK(rate_per_minute > 0 AND writes_per_minute > 0 AND max_read > 0 AND max_write > 0 AND max_delete > 0)',
         'Limits must be greater than zero.'),
    ]

    @api.model
    def _default_expiry(self):
        days = _setting(self.env, 'default_expiry_days')
        if not days:
            return False
        return fields.Datetime.to_string(datetime.utcnow() + timedelta(days=days))

    @api.depends('revoked_at', 'expires_at')
    def _compute_state(self):
        now = datetime.utcnow()
        soon = now + timedelta(days=7)
        for key in self:
            expires = key.expires_at and fields.Datetime.from_string(key.expires_at)
            if key.revoked_at:
                key.state = 'revoked'
            elif expires and expires <= now:
                key.state = 'expired'
            elif expires and expires <= soon:
                key.state = 'expiring'
            else:
                key.state = 'active'

    def _search_state(self, operator, value):
        now = fields.Datetime.now()
        soon = fields.Datetime.to_string(datetime.utcnow() + timedelta(days=7))
        domains = {
            'revoked': [('revoked_at', '!=', False)],
            'expired': [('revoked_at', '=', False), ('expires_at', '!=', False), ('expires_at', '<=', now)],
            'expiring': [('revoked_at', '=', False), ('expires_at', '>', now), ('expires_at', '<=', soon)],
            'active': [('revoked_at', '=', False), '|', ('expires_at', '=', False), ('expires_at', '>', soon)],
        }
        values = value if isinstance(value, (list, tuple)) else [value]
        if operator in ('=', 'in'):
            wanted = values
        elif operator in ('!=', 'not in'):
            wanted = [state for state, _label in STATES if state not in values]
        else:
            raise UserError(_("Unsupported search on the key status."))
        if not wanted:
            return [('id', '=', 0)]
        return expression.normalize_domain(expression.OR([domains[state] for state in wanted]))

    def _compute_sql_enabled(self):
        enabled = grants.read_settings(self.env.cr)['sql_enabled']
        for key in self:
            key.sql_enabled = enabled

    @api.constrains('allow_sql')
    def _check_sql(self):
        if any(key.allow_sql for key in self) and not grants.read_settings(self.env.cr)['sql_enabled']:
            raise ValidationError(_("SQL through MCP is switched off in MCP Settings."))
        for key in self.filtered('allow_sql'):
            if key.user_id.has_group('base.group_system'):
                raise ValidationError(_(
                    "A key that acts as a Settings administrator cannot use SQL. "
                    "Bind it to a user without the Administration / Settings group."))

    @api.constrains('user_id')
    def _check_sql_user(self):
        self._check_sql()

    @api.depends('permission_ids.can_read', 'permission_ids.can_create', 'permission_ids.can_update',
                 'permission_ids.can_delete', 'permission_ids.can_call', 'allow_methods', 'allow_sql',
                 'sql_table_ids.can_delete')
    def _compute_permission_summary(self):
        for key in self:
            rows = key.permission_ids.filtered(lambda row: row.can_read or row.can_create or row.can_update
                                               or row.can_delete or (row.can_call and key.allow_methods))
            tables = key.allow_sql and key.sql_table_ids or key.sql_table_ids.browse()
            key.model_count = len(rows) + len(tables)
            key.can_delete = any(row.can_delete for row in rows) or any(row.can_delete for row in tables)

    def _compute_usage(self):
        stats = {}
        if self.ids:
            self.env.cr.execute("""
                SELECT key_id, max(create_date),
                       sum(CASE WHEN create_date > (now() AT TIME ZONE 'UTC') - interval '24 hours' THEN 1 ELSE 0 END),
                       sum(CASE WHEN create_date > (now() AT TIME ZONE 'UTC') - interval '24 hours'
                                 AND result IN ('refused', 'limited') THEN 1 ELSE 0 END)
                  FROM kaitech_mcp_call WHERE key_id IN %s GROUP BY key_id""", (tuple(self.ids),))
            stats = dict((row[0], row[1:]) for row in self.env.cr.fetchall())
        for key in self:
            last, calls, refused = stats.get(key.id, (False, 0, 0))
            key.last_used_at = last
            key.calls_24h = calls
            key.refused_24h = refused

    def _compute_endpoint_url(self):
        url = self.env['kaitech.mcp.overview'].endpoint_url_for(self.env.cr.dbname)
        for key in self:
            key.endpoint_url = url

    @api.constrains('user_id')
    def _check_user(self):
        for key in self:
            if key.user_id.id == SUPERUSER_ID:
                raise ValidationError(_(
                    "An MCP key cannot act as the Administrator (uid 1): that user bypasses every access right "
                    "and record rule. Create a dedicated user for this client with only the groups it needs."))
            if key.user_id.share:
                raise ValidationError(_("An MCP key must act as an internal user, not a portal or public user."))
            check_within_your_groups(self.env, key.user_id)

    @api.constrains('rate_per_minute', 'writes_per_minute', 'max_read', 'max_write', 'max_delete')
    def _check_limits(self):
        for key in self:
            if key.max_delete > grants.HARD_MAX_DELETE:
                raise ValidationError(_("At most %s records can be deleted per call.") % grants.HARD_MAX_DELETE)
            if key.max_read > grants.HARD_MAX_READ or key.max_write > grants.HARD_MAX_READ:
                raise ValidationError(_("At most %s records can be read or updated per call.") % grants.HARD_MAX_READ)

    @api.model
    def create(self, values):
        values = dict(values)
        values.pop('key_hash', None)
        values.pop('key_index', None)
        return super(McpKey, self).create(values)

    @api.multi
    def write(self, values):
        if 'key_hash' in values or 'key_index' in values:
            raise UserError(_("A key's secret cannot be changed; rotate the key instead."))
        return super(McpKey, self).write(values)

    @api.multi
    def _issue_token(self):
        """Give this key a new secret and return it; only its digest is stored."""
        self.ensure_one()
        token = keys.new_token()
        self.env.cr.execute("UPDATE kaitech_mcp_key SET key_hash = %s, key_index = %s WHERE id = %s",
                            (keys.digest(token), keys.index_of(token), self.id))
        self.invalidate_cache(['key_hash', 'key_index'], self.ids)
        return token

    @api.multi
    def action_revoke(self):
        for key in self.filtered(lambda k: not k.revoked_at):
            key.write({'revoked_at': fields.Datetime.now(), 'revoked_by': self.env.uid})
        return True

    @api.multi
    def action_rotate(self):
        self.ensure_one()
        if self.revoked_at:
            raise UserError(_("A revoked key cannot be rotated; create a new key instead."))
        replacement = self.copy({'name': self.name, 'rotated_from_id': self.id})
        token = replacement._issue_token()
        self.action_revoke()
        wizard = self.env['kaitech.mcp.key.wizard'].create({
            'name': replacement.name, 'user_id': replacement.user_id.id, 'step': 'done',
            'key_id': replacement.id, 'token': token,
        })
        return wizard._reopen(_('MCP key rotated: copy the new key'))

    @api.multi
    def action_test_key(self):
        self.ensure_one()
        wizard = self.env['kaitech.mcp.key.test'].create({'key_id': self.id})
        return {
            'type': 'ir.actions.act_window', 'res_model': 'kaitech.mcp.key.test', 'res_id': wizard.id,
            'view_mode': 'form', 'target': 'new', 'name': _('Test MCP key'),
        }

    @api.multi
    def action_open_calls(self):
        self.ensure_one()
        action = self.env.ref('kaitech_mcp_server.action_mcp_call').read()[0]
        action['domain'] = [('key_id', '=', self.id)]
        action['context'] = {}
        return action
