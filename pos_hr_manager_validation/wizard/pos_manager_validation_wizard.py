from odoo import _, api, fields, models
from odoo.exceptions import AccessError

TARGETS = {
    ('pos.session', 'action_pos_session_closing_control'),
    ('pos.order', 'refund'),
    ('pos.make.payment', 'check'),
}


class PosManagerValidationWizard(models.TransientModel):
    _name = 'pos.manager.validation.wizard'
    _description = 'POS Manager Validation'

    config_id = fields.Many2one('pos.config', required=True, readonly=True)
    validation_action = fields.Selection(
        selection=lambda self: self.env['pos.manager.validation']._fields['action'].selection,
        required=True, readonly=True)
    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method = fields.Char(required=True, readonly=True)
    order_id = fields.Many2one('pos.order', readonly=True)
    allowed_employee_ids = fields.Many2many(related='config_id.mv_employee_ids')
    employee_id = fields.Many2one(
        'hr.employee', string='Manager', domain="[('id', 'in', allowed_employee_ids)]")
    pin = fields.Char(string='Validation PIN', compute='_compute_pin', inverse='_inverse_pin')
    validated = fields.Boolean(readonly=True)
    message = fields.Char(readonly=True)

    @api.model
    def _open_for(self, config, action, record, method, order=False):
        managers = config.sudo().mv_employee_ids
        wizard = self.create({
            'config_id': config.id,
            'validation_action': action,
            'res_model': record._name,
            'res_id': record.id,
            'method': method,
            'order_id': order.id if order else False,
            'employee_id': managers[:1].id if len(managers) == 1 else False,
        })
        return wizard._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Manager Validation'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _reference(self):
        if self.res_model == 'pos.make.payment':
            return 'pos.order,%s' % self.order_id.id
        return '%s,%s' % (self.res_model, self.res_id)

    def _compute_pin(self):
        self.pin = False

    def _inverse_pin(self):
        for wizard in self:
            if not wizard.pin:
                continue
            result = self.env['pos.manager.validation']._mv_verify(
                wizard.config_id, False, wizard.validation_action, wizard.employee_id, wizard.pin,
                reference=wizard._reference(), origin='backend')
            if result['status'] == 'granted':
                wizard.write({'validated': True, 'message': False})
            elif result['status'] == 'locked':
                wizard.write({'validated': False, 'message': _(
                    'Too many wrong PINs. Manager validation is locked until %s (UTC).', result['locked_until'])})
            else:
                wizard.write({'validated': False, 'message': _(
                    'Wrong PIN. %s attempt(s) left.', result['attempts_left'])})

    def action_confirm(self):
        self.ensure_one()
        if (self.res_model, self.method) not in TARGETS:
            raise AccessError(_('This action cannot be validated here.'))
        if not self.validated:
            return self._reopen()
        target = self.env[self.res_model].browse(self.res_id).exists()
        if self.order_id:
            target = target.with_context(
                active_id=self.order_id.id, active_ids=self.order_id.ids, active_model='pos.order')
        return getattr(target, self.method)()
