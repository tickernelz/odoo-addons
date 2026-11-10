from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    bank_card_manager_pin = fields.Char(
        string='Bank Card Manager PIN',
        groups='pos_bank_card_program.group_bank_card_manager', copy=False)
