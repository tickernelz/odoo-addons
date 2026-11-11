from passlib.context import CryptContext

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MIN_PIN_LENGTH = 6
PIN_CONTEXT = CryptContext(['pbkdf2_sha512'], pbkdf2_sha512__default_rounds=50000)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    pos_validation_pin = fields.Char(
        string='POS Validation PIN', compute='_compute_pos_validation_pin', inverse='_inverse_pos_validation_pin',
        groups='hr.group_hr_user', copy=False,
        help='Second PIN, at least six digits, that this employee types to validate sensitive point of sale actions. '
             'It is checked on the server only and never sent to the point of sale.')
    pos_validation_pin_hash = fields.Char(groups='base.group_system', copy=False)
    pos_validation_pin_set = fields.Boolean(
        string='POS Validation PIN Set', compute='_compute_pos_validation_pin_set', compute_sudo=True,
        groups='hr.group_hr_user')

    def _compute_pos_validation_pin(self):
        self.pos_validation_pin = False

    @api.depends('pos_validation_pin_hash')
    def _compute_pos_validation_pin_set(self):
        for employee in self:
            employee.pos_validation_pin_set = bool(employee.pos_validation_pin_hash)

    def _inverse_pos_validation_pin(self):
        for employee in self:
            pin = employee.pos_validation_pin or ''
            if not pin:
                if self.env['pos.config'].sudo().search_count([('mv_employee_ids', 'in', employee.ids)], limit=1):
                    raise ValidationError(_(
                        '%s validates actions on a point of sale, so the POS validation PIN cannot be removed.',
                        employee.name))
                employee.sudo().pos_validation_pin_hash = False
                continue
            if not pin.isdigit() or len(pin) < MIN_PIN_LENGTH:
                raise ValidationError(_('The POS validation PIN must have at least %s digits, digits only.', MIN_PIN_LENGTH))
            if employee.sudo().pin == pin:
                raise ValidationError(_('The POS validation PIN must differ from the PIN used to log in to the point of sale.'))
            employee.sudo().pos_validation_pin_hash = PIN_CONTEXT.hash(pin)

    @api.constrains('pin')
    def _check_pin_differs_from_validation_pin(self):
        for employee in self.sudo():
            if employee.pin and employee.pos_validation_pin_hash \
                    and PIN_CONTEXT.verify(employee.pin, employee.pos_validation_pin_hash):
                raise ValidationError(_('The PIN used to log in must differ from the POS validation PIN.'))

    def _pos_validation_pin_matches(self, pin):
        self.ensure_one()
        stored = self.sudo().pos_validation_pin_hash
        if not stored or not pin:
            PIN_CONTEXT.dummy_verify()
            return False
        return PIN_CONTEXT.verify(str(pin), stored)
