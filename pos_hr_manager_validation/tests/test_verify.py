import logging
from contextlib import contextmanager
from io import StringIO

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged, new_test_user

from .common import MANAGER_PIN, SECOND_MANAGER_PIN, ManagerValidationCommon


@contextmanager
def captured_logs():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


@tagged('post_install', '-at_install')
class TestVerify(ManagerValidationCommon):

    def setUp(self):
        super().setUp()
        self.guard('discount', 'payment')
        self.session = self.open_session()

    def failures(self):
        return self.Validation.search_count([('success', '=', False)])

    def test_correct_pin_is_granted_and_logged(self):
        result = self.verify(self.session, 'discount', MANAGER_PIN, reference='order-1')
        self.assertEqual(result, {'status': 'granted', 'remember': False})
        row = self.Validation.search([], limit=1)
        self.assertRecordValues(row, [{
            'action': 'discount', 'success': True, 'employee_id': self.manager.id,
            'session_id': self.session.id, 'reference': 'order-1', 'origin': 'pos'}])

    def test_fifth_wrong_pin_locks_even_the_right_pin(self):
        statuses = [self.verify(self.session, 'discount', '000000') for _attempt in range(5)]
        self.assertEqual([status.get('attempts_left') for status in statuses[:4]], [4, 3, 2, 1])
        self.assertEqual(statuses[4]['status'], 'locked')
        self.assertEqual(self.verify(self.session, 'discount', MANAGER_PIN)['status'], 'locked')
        self.assertEqual(self.failures(), 5)

    def test_lock_follows_the_manager_into_the_backend(self):
        for _attempt in range(5):
            self.verify(self.session, 'discount', '000000')
        result = self.Validation._mv_verify(self.main_pos_config, False, 'discount', self.manager, MANAGER_PIN, origin='backend')
        self.assertEqual(result['status'], 'locked')

    def test_lock_covers_every_manager_of_the_session(self):
        for _attempt in range(5):
            self.verify(self.session, 'discount', '000000')
        self.assertEqual(self.verify(self.session, 'discount', SECOND_MANAGER_PIN, employee=self.second_manager)['status'], 'locked')
        result = self.Validation._mv_verify(
            self.main_pos_config, False, 'discount', self.second_manager, SECOND_MANAGER_PIN, origin='backend')
        self.assertEqual(result['status'], 'granted')

    def test_lock_ends_when_the_wrong_pins_are_older_than_the_window(self):
        for _attempt in range(5):
            self.verify(self.session, 'discount', '000000')
        self.env.cr.execute(
            "UPDATE pos_manager_validation SET create_date = create_date - interval '16 minutes' WHERE success IS NOT TRUE")
        self.Validation.invalidate_model(['create_date'])
        self.assertEqual(self.verify(self.session, 'discount', MANAGER_PIN)['status'], 'granted')

    def test_right_pin_resets_the_count(self):
        for _attempt in range(4):
            self.verify(self.session, 'discount', '000000')
        self.assertEqual(self.verify(self.session, 'discount', MANAGER_PIN)['status'], 'granted')
        result = self.verify(self.session, 'discount', '000000')
        self.assertEqual(result, {'status': 'wrong', 'attempts_left': 4})

    def test_unlock_clears_the_lock(self):
        for _attempt in range(5):
            self.verify(self.session, 'discount', '000000')
        self.Validation.search([], limit=1).with_user(self.pos_admin).action_unlock()
        self.assertEqual(self.verify(self.session, 'discount', MANAGER_PIN)['status'], 'granted')

    def test_pin_never_reaches_logs_or_rows(self):
        with captured_logs() as stream:
            self.verify(self.session, 'discount', '937164')
            self.verify(self.session, 'discount', MANAGER_PIN)
        text = stream.getvalue()
        self.assertNotIn('937164', text)
        self.assertNotIn(MANAGER_PIN, text)
        rows = self.Validation.search([]).read()
        self.assertNotIn(MANAGER_PIN, str(rows))
        self.assertNotIn('937164', str(rows))

    def test_requests_outside_the_settings_are_refused(self):
        with self.assertRaises(UserError):
            self.verify(self.session, 'refund', MANAGER_PIN)
        with self.assertRaises(AccessError):
            self.verify(self.session, 'discount', '5651', employee=self.cashier)
        outsider = new_test_user(self.env, login='mv_outsider', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.verify(self.session, 'discount', MANAGER_PIN, user=outsider)
        self.assertEqual(self.Validation.search_count([]), 0)

    def test_settings_off_keep_the_point_of_sale_unchanged(self):
        self.main_pos_config.write({'mv_discount': False, 'mv_payment': False})
        with self.assertRaises(UserError):
            self.verify(self.session, 'discount', MANAGER_PIN)
        self.main_pos_config.write({'mv_discount': True, 'module_pos_hr': False})
        self.assertFalse(self.main_pos_config._mv_guards('discount'))

    def test_remember_needs_the_manager_at_the_register(self):
        self.assertFalse(self.verify(self.session, 'discount', MANAGER_PIN, remember=True)['remember'])
        self.session.employee_id = self.manager
        self.assertTrue(self.verify(self.session, 'discount', MANAGER_PIN, remember=True)['remember'])
        self.assertEqual(self.Validation.search_count([('action', '=', 'presence')]), 1)
