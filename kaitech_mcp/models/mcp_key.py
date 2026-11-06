from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from .. import grants, keys

STATES = [('active', 'Active'), ('expiring', 'Expiring soon'), ('expired', 'Expired'), ('revoked', 'Revoked')]
SQL_HELP = (
    "SQL runs as the database itself: it bypasses Odoo access rights, record rules, Python constraints, "
    "computed fields and chatter tracking. Only the tables listed on the SQL tab can be used, only when SQL "
    "is also switched on in MCP Settings, and never when the key acts as an administrator."
)


EXPLORE_HELP = (
    "The client may read how the installed modules are built: models, fields, methods and their source code, "
    "views, actions, menus and other code files inside the module folders. These tools never return records, "
    "settings values or files outside the module folders."
)
CALL_HELP = (
    "The client may call public business methods, for example Confirm on a sales order, of the models ticked "
    "in the Call column. They run as the key's user with its access rights and record rules, and change whatever "
    "the same button changes in Odoo. Private methods and ORM methods such as write or unlink are always refused."
)


def _setting(env, name):
    return grants.current_settings(env)[name]


class McpKey(models.Model):
    _name = 'kaitech.mcp.key'
    _description = 'MCP Key'
    _order = 'revoked_at desc, name, id'

    name = fields.Char('Client', required=True, help="Who uses this key, for example 'Finance - Claude Desktop'.")
    user_id = fields.Many2one(
        'res.users', 'Acts as user', required=True, ondelete='restrict',
        domain=[('id', '!=', SUPERUSER_ID), ('share', '=', False)],
        help="Every call runs as this Odoo user, with all of its access rights and record rules.")
    key_index = fields.Char('Key prefix', readonly=True, index=True, copy=False)
    key_hash = fields.Char(readonly=True, copy=False, groups='base.group_system')
    expires_at = fields.Datetime(
        'Expires on', copy=False, default=lambda self: self._default_expiry(),
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
    allow_sql = fields.Boolean('Allow SQL', copy=True, help=SQL_HELP)
    sql_table_ids = fields.One2many('kaitech.mcp.sql.table', 'key_id', 'SQL tables', copy=True, help=SQL_HELP)
    sql_enabled = fields.Boolean('SQL allowed in settings', compute='_compute_sql_enabled')
    allow_explore = fields.Boolean('Explore code', copy=True, help=EXPLORE_HELP)
    allow_call = fields.Boolean('Call methods', copy=True, help=CALL_HELP)
    call_ids = fields.One2many('kaitech.mcp.call', 'key_id', 'Calls', readonly=True)
    state = fields.Selection(STATES, 'Status', compute='_compute_state', search='_search_state')
    model_count = fields.Integer('Models and tables', compute='_compute_permission_summary')
    can_delete = fields.Boolean('Delete', compute='_compute_permission_summary')
    last_used_at = fields.Datetime('Last used', compute='_compute_usage')
    calls_24h = fields.Integer('Calls (24 h)', compute='_compute_usage')
    refused_24h = fields.Integer('Refused or limited (24 h)', compute='_compute_usage')
    endpoint_url = fields.Char('Endpoint', compute='_compute_endpoint_url')

    _positive_limits = models.Constraint(
        'CHECK(rate_per_minute > 0 AND writes_per_minute > 0 AND max_read > 0 AND max_write > 0 AND max_delete > 0)',
        'Limits must be greater than zero.')

    @api.model
    def _default_expiry(self):
        days = _setting(self.env, 'default_expiry_days')
        if not days:
            return False
        return fields.Datetime.now() + timedelta(days=days)

    @api.depends('revoked_at', 'expires_at')
    def _compute_state(self):
        now = datetime.utcnow()
        soon = now + timedelta(days=7)
        for key in self:
            if key.revoked_at:
                key.state = 'revoked'
            elif key.expires_at and key.expires_at <= now:
                key.state = 'expired'
            elif key.expires_at and key.expires_at <= soon:
                key.state = 'expiring'
            else:
                key.state = 'active'

    def _search_state(self, operator, value):
        now = fields.Datetime.now()
        soon = now + timedelta(days=7)
        domains = {
            'revoked': [('revoked_at', '!=', False)],
            'expired': [('revoked_at', '=', False), ('expires_at', '!=', False), ('expires_at', '<=', now)],
            'expiring': [('revoked_at', '=', False), ('expires_at', '>', now), ('expires_at', '<=', soon)],
            'active': [('revoked_at', '=', False), '|', ('expires_at', '=', False), ('expires_at', '>', soon)],
        }
        values = [value] if isinstance(value, str) else list(value)
        if operator in ('=', 'in'):
            wanted = values
        elif operator in ('!=', 'not in'):
            wanted = [state for state, _label in STATES if state not in values]
        else:
            raise UserError(self.env._("Unsupported search on the key status."))
        return Domain.OR(Domain(domains[state]) for state in wanted)

    def _compute_sql_enabled(self):
        enabled = grants.current_settings(self.env)['sql_enabled']
        for key in self:
            key.sql_enabled = enabled

    @api.depends('permission_ids.can_read', 'permission_ids.can_create', 'permission_ids.can_update',
                 'permission_ids.can_delete', 'permission_ids.can_call', 'allow_call', 'allow_sql', 'sql_table_ids.can_delete')
    def _compute_permission_summary(self):
        for key in self:
            rows = key.permission_ids.filtered(lambda row: row.can_read or row.can_create or row.can_update or row.can_delete
                                               or (row.can_call and key.allow_call))
            tables = key.sql_table_ids if key.allow_sql else key.sql_table_ids.browse()
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
            stats = {row[0]: row[1:] for row in self.env.cr.fetchall()}
        for key in self:
            last, calls, refused = stats.get(key.id, (False, 0, 0))
            key.last_used_at = last
            key.calls_24h = calls
            key.refused_24h = refused

    def _compute_endpoint_url(self):
        url = self.env['kaitech.mcp.overview'].endpoint_url_for(self.env.cr.dbname)
        for key in self:
            key.endpoint_url = url

    @api.constrains('user_id', 'allow_sql')
    def _check_user(self):
        admin = self.env.ref('base.user_admin', raise_if_not_found=False)
        for key in self:
            if key.user_id.id == SUPERUSER_ID or (admin and key.user_id == admin):
                raise ValidationError(self.env._(
                    "An MCP key cannot act as the Administrator: that user holds every access right. "
                    "Create a dedicated user for this client with only the groups it needs."))
            if key.user_id.share:
                raise ValidationError(self.env._("An MCP key must act as an internal user, not a portal or public user."))
            if key.allow_sql and self.env.ref('base.group_system') in key.user_id.sudo().all_group_ids:
                raise ValidationError(self.env._(
                    "A key with SQL cannot act as a Settings administrator."))

    def _check_within_your_groups(self):
        """Refuse a key acting as a user with access groups the current user lacks, unless that is an administrator."""
        creator = self.env.user
        if self.env.uid == SUPERUSER_ID or creator._is_system():
            return
        for key in self:
            if key.user_id.sudo().all_group_ids - creator.sudo().all_group_ids:
                raise ValidationError(self.env._(
                    "An MCP key can only act as a user whose access groups you hold yourself."))

    @api.constrains('allow_sql')
    def _check_sql(self):
        if any(key.allow_sql for key in self) and not grants.current_settings(self.env)['sql_enabled']:
            raise ValidationError(self.env._("SQL through MCP is switched off in MCP Settings."))

    @api.constrains('rate_per_minute', 'writes_per_minute', 'max_read', 'max_write', 'max_delete')
    def _check_limits(self):
        for key in self:
            if key.max_delete > grants.HARD_MAX_DELETE:
                raise ValidationError(self.env._("At most %s records can be deleted per call.", grants.HARD_MAX_DELETE))
            if key.max_read > grants.HARD_MAX_READ or key.max_write > grants.HARD_MAX_READ:
                raise ValidationError(self.env._("At most %s records can be read or updated per call.", grants.HARD_MAX_READ))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [{name: value for name, value in vals.items() if name not in ('key_hash', 'key_index')}
                     for vals in vals_list]
        keys = super().create(vals_list)
        keys._check_within_your_groups()
        return keys

    def write(self, vals):
        if 'key_hash' in vals or 'key_index' in vals:
            raise UserError(self.env._("A key's secret cannot be changed; rotate the key instead."))
        result = super().write(vals)
        if 'user_id' in vals or vals.get('allow_sql'):
            self._check_within_your_groups()
        return result

    def _issue_token(self):
        """Give this key a new secret and return it; only its digest is stored."""
        self.ensure_one()
        token = keys.new_token()
        self.env.cr.execute("UPDATE kaitech_mcp_key SET key_hash = %s, key_index = %s WHERE id = %s",
                            (keys.digest(token), keys.index_of(token), self.id))
        self.invalidate_recordset(['key_hash', 'key_index'])
        return token

    def action_revoke(self):
        for key in self.filtered(lambda k: not k.revoked_at):
            key.write({'revoked_at': fields.Datetime.now(), 'revoked_by': self.env.uid})
        return True

    def action_rotate(self):
        self.ensure_one()
        if self.revoked_at:
            raise UserError(self.env._("A revoked key cannot be rotated; create a new key instead."))
        replacement = self.copy({'name': self.name, 'rotated_from_id': self.id})
        token = replacement._issue_token()
        self.action_revoke()
        wizard = self.env['kaitech.mcp.key.wizard'].create({
            'name': replacement.name, 'user_id': replacement.user_id.id, 'step': 'done',
            'key_id': replacement.id, 'token': token,
        })
        return wizard._reopen(self.env._('MCP key rotated: copy the new key'))

    def action_test_key(self):
        self.ensure_one()
        wizard = self.env['kaitech.mcp.key.test'].create({'key_id': self.id})
        return wizard._reopen()

    def action_open_calls(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('kaitech_mcp.action_mcp_call')
        action['domain'] = [('key_id', '=', self.id)]
        action['context'] = {}
        return action
