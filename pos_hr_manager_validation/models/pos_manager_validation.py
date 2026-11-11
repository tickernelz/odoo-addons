from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .pos_config import GUARDED_ACTIONS

MAX_FAILURES = 5
FAILURE_WINDOW_MINUTES = 15
GRANT_MINUTES = 10
ADVISORY_LOCK_NAMESPACE = 56350


class PosManagerValidation(models.Model):
    _name = 'pos.manager.validation'
    _description = 'POS Manager Validation'
    _order = 'id desc'

    config_id = fields.Many2one('pos.config', string='Point of Sale', required=True, index=True, ondelete='cascade')
    session_id = fields.Many2one('pos.session', string='Session', index=True, ondelete='set null')
    employee_id = fields.Many2one('hr.employee', string='Manager', required=True, index=True, ondelete='restrict')
    cashier_id = fields.Many2one('hr.employee', string='Cashier', ondelete='set null')
    action = fields.Selection([
        ('close_session', 'Close Session'),
        ('delete_order', 'Delete Order'),
        ('delete_line', 'Delete Order Line'),
        ('payment', 'Payment'),
        ('discount', 'Discount'),
        ('price', 'Price Change'),
        ('decrease_qty', 'Quantity Decrease'),
        ('cash_move', 'Cash In/Out'),
        ('create_product', 'Product Creation'),
        ('fiscal_position', 'Fiscal Position'),
        ('pricelist', 'Pricelist'),
        ('refund', 'Refund'),
        ('presence', 'Manager at the Register'),
    ], required=True)
    success = fields.Boolean(string='Validated', readonly=True)
    origin = fields.Selection([
        ('pos', 'Point of Sale'),
        ('backend', 'Backend'),
        ('presence', 'Manager at the Register'),
    ], required=True, default='pos')
    reference = fields.Char(index=True)
    consumed = fields.Boolean(readonly=True)
    cleared = fields.Boolean(string='Unlocked', readonly=True)

    def _scope_domains(self, config, session, employee):
        if session:
            scope = [('session_id', '=', session.id)]
        else:
            scope = [('session_id', '=', False), ('config_id', '=', config.id), ('create_uid', '=', self.env.uid)]
        return [[('employee_id', '=', employee.id)], scope]

    def _lock_state(self, domain):
        now = fields.Datetime.now()
        window = domain + [
            ('success', '=', False),
            ('cleared', '=', False),
            ('create_date', '>=', now - timedelta(minutes=FAILURE_WINDOW_MINUTES)),
        ]
        reset = self.search(domain + [('success', '=', True), ('action', '!=', 'presence')], limit=1)
        if reset:
            window.append(('id', '>', reset.id))
        failures = self.search(window, limit=MAX_FAILURES, order='id desc')
        if len(failures) >= MAX_FAILURES:
            locked_until = failures[-1].create_date + timedelta(minutes=FAILURE_WINDOW_MINUTES)
            if locked_until > now:
                return locked_until, 0
        return False, MAX_FAILURES - len(failures)

    def _locks(self, config, session, employee):
        states = [self._lock_state(domain) for domain in self._scope_domains(config, session, employee)]
        locked_until = max((until for until, _left in states if until), default=False)
        return locked_until, min(left for _until, left in states)

    @api.model
    def _mv_verify(self, config, session, action, employee, pin, reference=False, remember=False, origin='pos'):
        Validation = self.sudo()
        if action not in GUARDED_ACTIONS or not config._mv_guards(action):
            raise UserError(_('This action does not need a manager validation on %s.', config.name))
        if not employee or employee not in config.sudo().mv_employee_ids:
            raise AccessError(_('This employee cannot validate actions on %s.', config.name))
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (ADVISORY_LOCK_NAMESPACE, employee.id))
        locked_until, _left = Validation._locks(config, session, employee)
        if locked_until:
            return {'status': 'locked', 'locked_until': fields.Datetime.to_string(locked_until)}
        granted = employee._pos_validation_pin_matches(pin)
        cashier = session.employee_id if session and 'employee_id' in session._fields else self.env['hr.employee']
        Validation.create({
            'config_id': config.id,
            'session_id': session.id if session else False,
            'employee_id': employee.id,
            'cashier_id': cashier.id,
            'action': action,
            'success': granted,
            'origin': origin,
            'reference': reference or False,
        })
        if granted:
            remembered = bool(remember and session and cashier == employee)
            if remembered:
                Validation.create({
                    'config_id': config.id,
                    'session_id': session.id,
                    'employee_id': employee.id,
                    'cashier_id': employee.id,
                    'action': 'presence',
                    'success': True,
                    'origin': 'presence',
                })
            return {'status': 'granted', 'remember': remembered}
        locked_until, left = Validation._locks(config, session, employee)
        if locked_until:
            return {'status': 'locked', 'locked_until': fields.Datetime.to_string(locked_until)}
        return {'status': 'wrong', 'attempts_left': left}

    def _presence(self, session):
        if not session or 'employee_id' not in session._fields or not session.employee_id:
            return self.browse()
        return self.sudo().search([
            ('session_id', '=', session.id),
            ('employee_id', '=', session.employee_id.id),
            ('action', '=', 'presence'),
            ('success', '=', True),
            ('consumed', '=', False),
        ], limit=1)

    @api.model
    def _mv_find_grant(self, config, action, references, session=False, include_consumed=False):
        domain = [
            ('config_id', '=', config.id),
            ('action', '=', action),
            ('success', '=', True),
            ('reference', 'in', list(references)),
            ('create_date', '>=', fields.Datetime.now() - timedelta(minutes=GRANT_MINUTES)),
        ]
        if not include_consumed:
            domain.append(('consumed', '=', False))
        return self.sudo().search(domain, limit=1)

    @api.model
    def _mv_require(self, config, action, reference, session=False, consume=True):
        if self.env.su or not config._mv_guards(action):
            return True
        presence = self._presence(session)
        if presence and presence.employee_id in config.sudo().mv_employee_ids:
            if consume:
                self._mv_find_grant(config, action, [reference]).write({'consumed': True})
            self.sudo().create({
                'config_id': config.id,
                'session_id': session.id,
                'employee_id': presence.employee_id.id,
                'cashier_id': presence.employee_id.id,
                'action': action,
                'success': True,
                'origin': 'presence',
                'reference': reference,
                'consumed': True,
            })
            return True
        grant = self._mv_find_grant(config, action, [reference], session=session)
        if not grant:
            raise UserError(_('A manager must validate this action with a POS validation PIN first.'))
        if consume:
            grant.consumed = True
        return grant

    @api.model
    def _mv_consume(self, config, action, reference):
        self._mv_find_grant(config, action, [reference]).write({'consumed': True})

    def action_unlock(self):
        failures = self.sudo().search([
            ('success', '=', False),
            ('cleared', '=', False),
            '|', ('employee_id', 'in', self.employee_id.ids), ('session_id', 'in', self.session_id.ids),
        ])
        failures.write({'cleared': True})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('Manager validation is unlocked for %s.', ', '.join(self.employee_id.mapped('name'))),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
