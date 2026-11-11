from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

DAYS = ('day_mon', 'day_tue', 'day_wed', 'day_thu', 'day_fri', 'day_sat', 'day_sun')
LIVE_STATES = ('to_approve', 'approved', 'active')
DRAFT_ONLY_FIELDS = {
    'name', 'promo_type', 'company_id', 'date_start', 'date_end', 'config_ids', 'product_ids', 'categ_ids',
    'excluded_product_ids', 'excluded_categ_ids', 'exclusive_group', 'priority', 'discount_product_id',
    'max_discount', 'rounding', 'happy_hour', 'tier_ids', 'schedule_ids',
}
TRANSITIONS = {
    'draft': {'to_approve'},
    'to_approve': {'approved', 'draft', 'stopped'},
    'approved': {'active', 'done', 'stopped'},
    'active': {'done', 'stopped'},
    'stopped': {'draft'},
    'done': set(),
}
APPROVED_STATES = {'approved', 'active'}
MAX_TIERS = 10
MAX_SCHEDULES = 4


class KaiPosPromo(models.Model):
    _name = 'kai.pos.promo'
    _description = 'POS Promotion'
    _inherit = ['pos.load.mixin', 'mail.thread']
    _order = 'priority, id'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    promo_type = fields.Selection([('tier', 'Tiered')], string='Type', required=True, default='tier', tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('to_approve', 'Waiting Approval'),
        ('approved', 'Approved'),
        ('active', 'Active'),
        ('done', 'Ended'),
        ('stopped', 'Stopped'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_start = fields.Date(string='Start Date', required=True, default=fields.Date.context_today, tracking=True)
    date_end = fields.Date(string='End Date', required=True, tracking=True)
    config_ids = fields.Many2many(
        'pos.config', string='Points of Sale', help='Leave empty to run the promotion in every point of sale.')
    product_ids = fields.Many2many(
        'product.product', 'kai_pos_promo_product_rel', string='Products',
        help='Leave products and categories empty to count every product.')
    categ_ids = fields.Many2many('product.category', 'kai_pos_promo_categ_rel', string='Product Categories')
    excluded_product_ids = fields.Many2many(
        'product.product', 'kai_pos_promo_excluded_product_rel', string='Excluded Products')
    excluded_categ_ids = fields.Many2many(
        'product.category', 'kai_pos_promo_excluded_categ_rel', string='Excluded Categories')
    exclusive_group = fields.Char(
        default='ORDER-TIER', tracking=True,
        help='Within one group only the largest discount applies to an order; on equal discounts the lower '
             'priority number wins. Discount & Loyalty programmes can join the same group.')
    priority = fields.Integer(default=10, required=True, tracking=True)
    discount_product_id = fields.Many2one(
        'product.product', string='Discount Product', required=True,
        default=lambda self: self.env.ref('kai_pos_promotion.product_kai_promo_discount', raise_if_not_found=False),
        help='Product of the discount line; its income account receives the discount.')
    max_discount = fields.Monetary(string='Maximum Discount', help='Cap for every tier. Zero means no cap.')
    rounding = fields.Float(
        string='Discount Rounding', default=1.0, required=True,
        help='The discount is rounded half up to a multiple of this amount, once per promotion and order.')
    happy_hour = fields.Boolean(tracking=True)
    schedule_ids = fields.One2many('kai.pos.promo.schedule', 'promo_id', string='Happy Hour Windows')
    tier_ids = fields.One2many('kai.pos.promo.tier', 'promo_id', string='Tiers')
    conflict_html = fields.Html(compute='_compute_conflict_html', sanitize=False)

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for promo in self:
            if promo.date_end < promo.date_start:
                raise ValidationError(_('The end date of %s is before its start date.', promo.name))

    @api.constrains('rounding', 'max_discount')
    def _check_amounts(self):
        for promo in self:
            if promo.rounding <= 0:
                raise ValidationError(_('The discount rounding of %s must be positive.', promo.name))
            if promo.max_discount < 0:
                raise ValidationError(_('The maximum discount of %s cannot be negative.', promo.name))

    @api.constrains('name', 'date_start', 'date_end', 'state', 'company_id')
    def _check_name_unique(self):
        for promo in self.filtered(lambda p: p.state != 'stopped'):
            overlapping = self.search([
                ('id', '!=', promo.id),
                ('company_id', '=', promo.company_id.id),
                ('state', '!=', 'stopped'),
                ('date_start', '<=', promo.date_end),
                ('date_end', '>=', promo.date_start),
            ])
            if any(other.name.strip().lower() == promo.name.strip().lower() for other in overlapping):
                raise ValidationError(_('Another promotion named %s runs in an overlapping period.', promo.name))

    @api.constrains('tier_ids')
    def _check_tier_count(self):
        for promo in self:
            if len(promo.tier_ids) > MAX_TIERS:
                raise ValidationError(_('A promotion has at most %s tiers.', MAX_TIERS))
            thresholds = promo.tier_ids.mapped('threshold')
            if len(set(thresholds)) != len(thresholds):
                raise ValidationError(_('Every tier of %s needs its own spending threshold.', promo.name))

    @api.depends('exclusive_group', 'config_ids', 'date_start', 'date_end', 'company_id')
    def _compute_conflict_html(self):
        for promo in self:
            promo.conflict_html = False
            group = (promo.exclusive_group or '').strip()
            if not group or not promo.date_start or not promo.date_end:
                continue
            others = self.search([
                ('id', '!=', promo._origin.id or 0),
                ('exclusive_group', '=', group),
                ('state', 'in', LIVE_STATES),
                ('company_id', '=', promo.company_id.id),
                ('date_start', '<=', promo.date_end),
                ('date_end', '>=', promo.date_start),
            ])
            others = others.filtered(
                lambda other: not other.config_ids or not promo.config_ids or other.config_ids & promo.config_ids)
            programs = self.env['loyalty.program'].search([
                ('kai_exclusive_group', '=', group),
                ('company_id', 'in', [promo.company_id.id, False]),
                ('pos_ok', '=', True),
            ])
            if not others and not programs:
                continue
            names = Markup('').join(
                Markup('<li>%s</li>') % name for name in others.mapped('name') + programs.mapped('name'))
            promo.conflict_html = Markup(
                '<div class="alert alert-warning mb-0" role="alert"><b>%s</b><ul class="mb-0">%s</ul><div>%s</div></div>'
            ) % (
                _('Shares group %(group)s with %(count)s other promotion(s):', group=group, count=len(others) + len(programs)),
                names,
                _('Only the largest discount of the group applies to an order; on equal discounts the lower priority number wins.'),
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.is_superuser() and any(vals.get('state', 'draft') != 'draft' for vals in vals_list):
            raise UserError(_('A new promotion always starts as a draft.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'state' in vals:
            self._check_transition(vals['state'])
        if DRAFT_ONLY_FIELDS.intersection(vals):
            self._check_draft_children()
        return super().write(vals)

    def _check_transition(self, target):
        if self.env.is_superuser():
            return
        labels = dict(self._fields['state']._description_selection(self.env))
        for promo in self:
            if target != promo.state and target not in TRANSITIONS[promo.state]:
                raise UserError(_('%(promotion)s cannot go from %(old)s to %(new)s.',
                                  promotion=promo.name, old=labels[promo.state], new=labels.get(target, target)))
        if target in APPROVED_STATES and not self.env.user.has_group('kai_pos_promotion.group_kai_promo_approver'):
            raise AccessError(_('Only a promotion approver can approve a promotion.'))

    def _check_draft_children(self):
        if any(promo.state != 'draft' for promo in self):
            raise UserError(_('Only a draft promotion can be changed. Set it back to draft first.'))

    @api.ondelete(at_uninstall=False)
    def _unlink_only_drafts(self):
        if any(promo.state != 'draft' for promo in self):
            raise UserError(_('Only a draft promotion can be deleted. Stop it instead.'))

    @api.ondelete(at_uninstall=False)
    def _unlink_unused(self):
        used = self.env['pos.order.line'].sudo().search([('kai_promo_id', 'in', self.ids)], limit=1).kai_promo_id
        if used:
            raise UserError(_('%s is used on point of sale orders and cannot be deleted. Archive it instead.', used.name))

    def web_resequence(self, specification, field_name='sequence', offset=0):
        if field_name != 'priority':
            return super().web_resequence(specification, field_name, offset)
        for priority, promo in enumerate(self, start=offset):
            if promo.state == 'draft':
                promo.write({'priority': priority})
        return self.web_read(specification)

    def action_submit(self):
        for promo in self:
            if promo.state != 'draft':
                raise UserError(_('Only a draft promotion can be submitted.'))
            if not promo.tier_ids:
                raise UserError(_('Add at least one tier to %s before submitting it.', promo.name))
            if promo.happy_hour and not promo.schedule_ids:
                raise UserError(_('Add at least one happy hour window to %s before submitting it.', promo.name))
        self.state = 'to_approve'

    def action_approve(self):
        if any(promo.state != 'to_approve' for promo in self):
            raise UserError(_('Only a promotion waiting for approval can be approved.'))
        self.state = 'approved'
        self._sync_state()

    def action_stop(self):
        if any(promo.state not in LIVE_STATES for promo in self):
            raise UserError(_('Only a submitted, approved or active promotion can be stopped.'))
        self.state = 'stopped'

    def action_reset_draft(self):
        if any(promo.state not in ('to_approve', 'stopped') for promo in self):
            raise UserError(_('Only a promotion waiting for approval or stopped can go back to draft.'))
        self.state = 'draft'

    def _sync_state(self):
        today = fields.Date.context_today(self)
        for promo in self:
            if promo.state in ('approved', 'active') and promo.date_end < today:
                promo.state = 'done'
            elif promo.state == 'approved' and promo.date_start <= today:
                promo.state = 'active'

    @api.model
    def _cron_sync_states(self):
        self.search([('state', 'in', ('approved', 'active'))])._sync_state()

    @api.model
    def _load_pos_data_domain(self, data, config):
        today = fields.Date.context_today(self)
        return [
            ('state', '=', 'active'),
            ('company_id', '=', config.company_id.id),
            ('date_start', '<=', today),
            ('date_end', '>=', today),
            '|', ('config_ids', '=', False), ('config_ids', 'in', config.ids),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'name', 'promo_type', 'product_ids', 'categ_ids', 'excluded_product_ids', 'excluded_categ_ids',
            'exclusive_group', 'priority', 'discount_product_id', 'max_discount', 'rounding', 'happy_hour',
            'schedule_ids', 'tier_ids',
        ]


class KaiPosPromoTier(models.Model):
    _name = 'kai.pos.promo.tier'
    _description = 'POS Promotion Tier'
    _inherit = ['pos.load.mixin']
    _order = 'promo_id, threshold'

    promo_id = fields.Many2one('kai.pos.promo', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='promo_id.currency_id')
    threshold = fields.Monetary(string='Spend at Least', required=True)
    discount_type = fields.Selection(
        [('percent', 'Percent'), ('amount', 'Amount per Order')], required=True, default='percent')
    value = fields.Float(required=True)

    @api.constrains('threshold', 'discount_type', 'value')
    def _check_values(self):
        for tier in self:
            if tier.threshold <= 0:
                raise ValidationError(_('A tier threshold must be positive.'))
            if tier.discount_type == 'percent' and not 0 < tier.value <= 100:
                raise ValidationError(_('A percentage tier must be above 0 and at most 100.'))
            if tier.discount_type == 'amount' and not 0 < tier.value < tier.threshold:
                raise ValidationError(_('An amount tier must be above 0 and below its threshold.'))

    @api.model_create_multi
    def create(self, vals_list):
        promo_ids = [vals['promo_id'] for vals in vals_list if vals.get('promo_id')]
        self.env['kai.pos.promo'].browse(promo_ids)._check_draft_children()
        return super().create(vals_list)

    def write(self, vals):
        self.promo_id._check_draft_children()
        return super().write(vals)

    def unlink(self):
        self.promo_id._check_draft_children()
        return super().unlink()

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('promo_id', 'in', [promo['id'] for promo in data.get('kai.pos.promo', [])])]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'promo_id', 'threshold', 'discount_type', 'value']


class KaiPosPromoSchedule(models.Model):
    _name = 'kai.pos.promo.schedule'
    _description = 'Happy Hour Window'
    _inherit = ['pos.load.mixin']
    _order = 'hour_from'

    promo_id = fields.Many2one('kai.pos.promo', ondelete='cascade', index='btree_not_null')
    program_id = fields.Many2one('loyalty.program', ondelete='cascade', index='btree_not_null')
    day_mon = fields.Boolean('Mon', default=True)
    day_tue = fields.Boolean('Tue', default=True)
    day_wed = fields.Boolean('Wed', default=True)
    day_thu = fields.Boolean('Thu', default=True)
    day_fri = fields.Boolean('Fri', default=True)
    day_sat = fields.Boolean('Sat', default=True)
    day_sun = fields.Boolean('Sun', default=True)
    hour_from = fields.Float('From', required=True, default=14.0)
    hour_to = fields.Float('To', required=True, default=16.0)

    @api.constrains('promo_id', 'program_id', 'hour_from', 'hour_to', *DAYS)
    def _check_window(self):
        for window in self:
            if bool(window.promo_id) == bool(window.program_id):
                raise ValidationError(_('A happy hour window belongs to exactly one promotion or programme.'))
            if not any(window[day] for day in DAYS):
                raise ValidationError(_('A happy hour window needs at least one day.'))
            if not 0 <= window.hour_from < window.hour_to <= 24:
                raise ValidationError(_(
                    'A happy hour window must end after it starts on the same day. '
                    'Split a window that crosses midnight into two rows.'))
        for windows in [promo.schedule_ids for promo in self.promo_id] + [program.kai_schedule_ids for program in self.program_id]:
            if len(windows) > MAX_SCHEDULES:
                raise ValidationError(_('At most %s happy hour windows are allowed.', MAX_SCHEDULES))
            for index, first in enumerate(windows):
                for second in windows[index + 1:]:
                    shared_day = any(first[day] and second[day] for day in DAYS)
                    if shared_day and first.hour_from < second.hour_to and second.hour_from < first.hour_to:
                        raise ValidationError(_('Happy hour windows of the same day cannot overlap.'))

    @api.model_create_multi
    def create(self, vals_list):
        promo_ids = [vals['promo_id'] for vals in vals_list if vals.get('promo_id')]
        self.env['kai.pos.promo'].browse(promo_ids)._check_draft_children()
        return super().create(vals_list)

    def write(self, vals):
        self.promo_id._check_draft_children()
        return super().write(vals)

    def unlink(self):
        self.promo_id._check_draft_children()
        return super().unlink()

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [
            '|',
            ('promo_id', 'in', [promo['id'] for promo in data.get('kai.pos.promo', [])]),
            ('program_id', 'in', [program['id'] for program in data.get('loyalty.program', [])]),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'promo_id', 'program_id', 'hour_from', 'hour_to', *DAYS]
