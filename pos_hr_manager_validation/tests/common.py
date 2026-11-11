from uuid import uuid4

from odoo import Command, fields
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

MANAGER_PIN = '482915'
SECOND_MANAGER_PIN = '734261'


class ManagerValidationCommon(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids += cls.env.ref('hr.group_hr_user')
        cls.pos_admin.group_ids += cls.env.ref('account.group_account_invoice')
        Employee = cls.env['hr.employee']
        company = cls.main_pos_config.company_id
        cls.cashier = Employee.create({'name': 'MV Cashier', 'company_id': company.id})
        cls.manager = Employee.create({
            'name': 'MV Manager', 'company_id': company.id, 'pin': '5651', 'pos_validation_pin': MANAGER_PIN})
        cls.second_manager = Employee.create({
            'name': 'MV Second Manager', 'company_id': company.id, 'pos_validation_pin': SECOND_MANAGER_PIN})
        cls.main_pos_config.write({
            'module_pos_hr': True,
            'basic_employee_ids': [Command.set((cls.cashier | cls.manager).ids)],
        })
        cls.product = cls.desk_pad.product_variant_id
        cls.Validation = cls.env['pos.manager.validation']

    def guard(self, *actions, config=None, managers=None):
        config = config or self.main_pos_config
        values = {'mv_employee_ids': [Command.set((managers or self.manager | self.second_manager).ids)]}
        values.update({'mv_' + action: True for action in actions})
        config.write(values)

    def open_session(self, config=None):
        config = config or self.main_pos_config
        config.with_user(self.pos_user).open_ui()
        session = config.current_session_id
        session.set_opening_control(0, None)
        return session

    def verify(self, session, action, pin, employee=None, user=None, **kwargs):
        return session.with_user(user or self.pos_user).verify_manager_pin(
            action, (employee or self.manager).id, pin, **kwargs)

    def order_data(self, session, discount=0.0, price=None, uuid=None, employee=None, refunded_line=None, qty=1):
        price = self.product.lst_price if price is None else price
        subtotal = price * qty * (1 - discount / 100.0)
        method = session.config_id.payment_method_ids[:1]
        line = {
            'product_id': self.product.id, 'qty': qty, 'price_unit': price, 'discount': discount,
            'price_subtotal': subtotal, 'price_subtotal_incl': subtotal, 'tax_ids': [],
            'price_type': 'manual' if price != self.product.lst_price else 'original', 'pack_lot_ids': [],
        }
        if refunded_line:
            line['refunded_orderline_id'] = refunded_line.id
        uuid = uuid or str(uuid4())
        data = {
            'amount_paid': subtotal, 'amount_return': 0, 'amount_tax': 0, 'amount_total': subtotal,
            'date_order': fields.Datetime.to_string(fields.Datetime.now()),
            'pricelist_id': session.config_id.pricelist_id.id, 'name': 'Order %s' % uuid,
            'last_order_preparation_change': '{}', 'lines': [Command.create(line)],
            'partner_id': False, 'session_id': session.id, 'uuid': uuid, 'user_id': self.pos_user.id,
            'to_invoice': False, 'state': 'paid',
            'payment_ids': [Command.create({
                'amount': subtotal, 'name': fields.Datetime.now(), 'payment_method_id': method.id})],
        }
        if employee:
            data['employee_id'] = employee.id
        return data

    def sync(self, session, *orders):
        self.env['pos.order'].with_user(self.pos_user).sync_from_ui(list(orders))
        return self.env['pos.order'].search([('uuid', 'in', [order['uuid'] for order in orders])])
