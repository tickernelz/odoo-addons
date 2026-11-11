from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

GUARDED_ACTIONS = (
    'close_session', 'delete_order', 'delete_line', 'payment', 'discount', 'price',
    'decrease_qty', 'cash_move', 'create_product', 'fiscal_position', 'pricelist', 'refund',
)
FLAGGED_ACTIONS = ('payment', 'discount', 'fiscal_position', 'pricelist', 'refund')


class PosConfig(models.Model):
    _inherit = 'pos.config'

    mv_employee_ids = fields.Many2many(
        'hr.employee', 'pos_config_mv_employee_rel', 'config_id', 'employee_id', string='Validating Managers',
        help='Employees whose POS validation PIN approves the actions ticked below on this point of sale.')
    mv_close_session = fields.Boolean('Validate Session Closing')
    mv_delete_order = fields.Boolean('Validate Order Deletion')
    mv_delete_line = fields.Boolean('Validate Order Line Deletion')
    mv_payment = fields.Boolean('Validate Payment')
    mv_discount = fields.Boolean('Validate Discount')
    mv_price = fields.Boolean('Validate Price Change')
    mv_decrease_qty = fields.Boolean('Validate Quantity Decrease')
    mv_cash_move = fields.Boolean('Validate Cash In/Out')
    mv_create_product = fields.Boolean('Validate Product Creation')
    mv_fiscal_position = fields.Boolean('Validate Fiscal Position')
    mv_pricelist = fields.Boolean('Validate Pricelist')
    mv_refund = fields.Boolean('Validate Refund')

    def _mv_guards(self, action):
        self.ensure_one()
        return bool(self.module_pos_hr and self.mv_employee_ids and self['mv_' + action])

    @api.constrains('mv_employee_ids', 'company_id')
    def _check_mv_employee_ids(self):
        for config in self:
            for employee in config.mv_employee_ids.sudo():
                if employee.company_id and employee.company_id != config.company_id:
                    raise ValidationError(_(
                        '%(employee)s belongs to another company than %(config)s.',
                        employee=employee.name, config=config.name))
                if not employee.pos_validation_pin_hash:
                    raise ValidationError(_(
                        '%s has no POS validation PIN. Set it in the HR Settings tab of the employee first.',
                        employee.name))

    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if read_records:
            managers = config.mv_employee_ids.sudo() if config.module_pos_hr else self.env['hr.employee']
            read_records[0]['_mv_managers'] = [{'id': employee.id, 'name': employee.name} for employee in managers]
        return read_records
