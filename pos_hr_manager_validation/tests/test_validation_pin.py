from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import MANAGER_PIN, ManagerValidationCommon


@tagged('post_install', '-at_install')
class TestValidationPin(ManagerValidationCommon):

    def test_validation_pin_is_stored_hashed_and_never_read_back(self):
        stored = self.manager.sudo().pos_validation_pin_hash
        self.assertTrue(stored.startswith('$pbkdf2-sha512$'))
        self.assertNotIn(MANAGER_PIN, stored)
        self.assertFalse(self.manager.pos_validation_pin)
        self.assertTrue(self.manager.pos_validation_pin_set)
        self.assertTrue(self.manager._pos_validation_pin_matches(MANAGER_PIN))
        self.assertFalse(self.manager._pos_validation_pin_matches('482916'))

    def test_validation_pin_needs_six_digits(self):
        for pin in ('12345', '12345a', 'abcdef', '1234567890123'):
            with self.assertRaises(ValidationError):
                self.second_manager.pos_validation_pin = pin

    def test_validation_pin_accepts_twelve_digits(self):
        self.second_manager.pos_validation_pin = '123456789012'
        self.assertTrue(self.second_manager._pos_validation_pin_matches('123456789012'))

    def test_validation_pin_differs_from_login_pin(self):
        self.manager.pin = '565100'
        with self.assertRaises(ValidationError):
            self.manager.pos_validation_pin = '565100'
        with self.assertRaises(ValidationError):
            self.manager.pin = MANAGER_PIN

    def test_validation_pin_of_a_validating_manager_cannot_be_removed(self):
        self.guard('discount')
        with self.assertRaises(ValidationError):
            self.manager.pos_validation_pin = False
        self.cashier.pos_validation_pin = '908172'
        self.cashier.pos_validation_pin = False
        self.assertFalse(self.cashier.pos_validation_pin_set)

    def test_manager_without_validation_pin_is_refused(self):
        with self.assertRaises(ValidationError):
            self.main_pos_config.mv_employee_ids = [Command.set(self.cashier.ids)]

    def test_validation_pin_never_reaches_the_point_of_sale(self):
        self.guard('discount', 'payment')
        session = self.open_session()
        payload = str(session.with_user(self.pos_user).load_data([]))
        self.assertNotIn(self.manager.sudo().pos_validation_pin_hash, payload)
        self.assertNotIn('pos_validation_pin', payload)
        self.assertIn("'_mv_managers'", payload)
        config_data = session.with_user(self.pos_user).load_data(['pos.config'])['pos.config'][0]
        self.assertEqual(
            sorted(manager['id'] for manager in config_data['_mv_managers']),
            sorted((self.manager | self.second_manager).ids))
