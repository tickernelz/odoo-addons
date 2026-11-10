import base64
import hashlib
import os

from odoo import api, fields, models

PIN_ITERATIONS = 120000


def hash_manager_pin(pin):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', str(pin).encode(), salt, PIN_ITERATIONS)
    return 'pbkdf2$%s$%s$%s' % (
        PIN_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def check_manager_pin(pin, stored):
    if not pin or not stored or not stored.startswith('pbkdf2$'):
        return False
    try:
        _scheme, iterations, salt, expected = stored.split('$')
        digest = hashlib.pbkdf2_hmac(
            'sha256', str(pin).encode(), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return base64.b64encode(digest).decode() == expected


class ResUsers(models.Model):
    _inherit = 'res.users'

    bank_card_manager_pin = fields.Char(
        string='Bank Card Manager PIN',
        groups='pos_bank_card_program.group_bank_card_manager', copy=False)
    bank_card_pin_attempts = fields.Integer(string='Bank Card PIN Attempts', default=0, copy=False)
    bank_card_pin_locked_until = fields.Datetime(string='Bank Card PIN Locked Until', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pin = vals.get('bank_card_manager_pin')
            if pin and not str(pin).startswith('pbkdf2$'):
                vals['bank_card_manager_pin'] = hash_manager_pin(pin)
        return super().create(vals_list)

    def write(self, vals):
        pin = vals.get('bank_card_manager_pin')
        if pin and not str(pin).startswith('pbkdf2$'):
            vals = dict(vals, bank_card_manager_pin=hash_manager_pin(pin))
        return super().write(vals)
