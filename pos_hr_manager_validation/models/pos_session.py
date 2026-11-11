from odoo import _, models
from odoo.exceptions import AccessError, UserError


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _mv_reference(self):
        self.ensure_one()
        return 'pos.session,%s' % self.id

    def verify_manager_pin(self, action, employee_id, pin, reference=False, remember=False):
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Only point of sale users can request a manager validation.'))
        if self.state not in ('opened', 'closing_control'):
            raise UserError(_('This session is not open.'))
        employee = self.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        return self.env['pos.manager.validation']._mv_verify(
            self.config_id, self, action, employee, pin, reference=reference, remember=remember, origin='pos')

    def write(self, vals):
        if vals.get('employee_id'):
            self.env['pos.manager.validation'].sudo().search([
                ('session_id', 'in', self.ids),
                ('action', '=', 'presence'),
                ('consumed', '=', False),
                ('employee_id', '!=', vals['employee_id']),
            ]).write({'consumed': True})
        return super().write(vals)

    def action_pos_session_closing_control(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        Validation = self.env['pos.manager.validation']
        guarded = self.filtered(lambda session: session.config_id._mv_guards('close_session'))
        for session in guarded:
            Validation._mv_require(session.config_id, 'close_session', session._mv_reference(), session=session, consume=False)
        result = super().action_pos_session_closing_control(
            balancing_account=balancing_account, amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs)
        for session in guarded.filtered(lambda session: session.state == 'closed'):
            Validation._mv_consume(session.config_id, 'close_session', session._mv_reference())
        return result

    def action_mv_closing_control(self):
        self.ensure_one()
        if self.config_id._mv_guards('close_session') and not self.env.su:
            return self.env['pos.manager.validation.wizard']._open_for(
                self.config_id, 'close_session', self, 'action_pos_session_closing_control')
        return self.action_pos_session_closing_control()

    def try_cash_in_out(self, _type, amount, reason, partner_id, extras):
        for session in self:
            self.env['pos.manager.validation']._mv_require(
                session.config_id, 'cash_move', session._mv_reference(), session=session)
        return super().try_cash_in_out(_type, amount, reason, partner_id, extras)
