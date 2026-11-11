from odoo import api, fields, models

from .pos_config import FLAGGED_ACTIONS


class PosOrder(models.Model):
    _inherit = 'pos.order'

    mv_unvalidated = fields.Char(string='Unvalidated Action Keys', readonly=True, copy=False)
    mv_flagged = fields.Boolean(string='Unvalidated Actions', readonly=True, copy=False, index='btree_not_null')
    mv_unvalidated_display = fields.Char(string='Actions Without Manager Validation', compute='_compute_mv_unvalidated_display')

    @api.depends('mv_unvalidated')
    def _compute_mv_unvalidated_display(self):
        labels = dict(self.env['pos.manager.validation']._fields['action']._description_selection(self.env))
        for order in self:
            keys = (order.mv_unvalidated or '').split(',')
            order.mv_unvalidated_display = ', '.join(labels[key] for key in keys if key in labels)

    def _mv_reference(self):
        self.ensure_one()
        return 'pos.order,%s' % self.id

    def action_pos_order_cancel(self):
        Validation = self.env['pos.manager.validation']
        for order in self.filtered(lambda order: order.state == 'draft' and order.lines):
            config = order.config_id
            if not config._mv_guards('delete_order') or config.module_pos_restaurant:
                continue
            if Validation._mv_find_grant(config, 'close_session', [order.session_id._mv_reference()], include_consumed=True):
                continue
            Validation._mv_require(config, 'delete_order', order.uuid, session=order.session_id)
        return super().action_pos_order_cancel()

    def refund(self):
        for order in self:
            self.env['pos.manager.validation']._mv_require(order.config_id, 'refund', order._mv_reference())
        return super().refund()

    def action_mv_refund(self):
        self.ensure_one()
        if self.config_id._mv_guards('refund') and not self.env.su:
            return self.env['pos.manager.validation.wizard']._open_for(self.config_id, 'refund', self, 'refund')
        return self.refund()

    @api.model
    def sync_from_ui(self, orders):
        result = super().sync_from_ui(orders)
        session_ids = {order.get('session_id') for order in orders if isinstance(order.get('session_id'), int)}
        configs = self.env['pos.session'].browse(session_ids).config_id
        if any(config._mv_guards(action) for config in configs for action in FLAGGED_ACTIONS):
            uuids = [order.get('uuid') for order in orders if order.get('uuid')]
            self.search([('uuid', 'in', uuids)])._mv_flag()
        return result

    def _mv_unvalidated_actions(self):
        self.ensure_one()
        config = self.config_id
        actions = [action for action in FLAGGED_ACTIONS if config._mv_guards(action)]
        if not actions:
            return []
        Validation = self.env['pos.manager.validation'].sudo()
        cashier = self.employee_id if 'employee_id' in self._fields else self.env['hr.employee']
        if cashier and cashier in config.sudo().mv_employee_ids and Validation.search_count([
            ('session_id', '=', self.session_id.id), ('employee_id', '=', cashier.id),
            ('action', '=', 'presence'), ('success', '=', True)], limit=1):
            return []
        refunded = self.lines.refunded_orderline_id.order_id
        references = [self.uuid] + refunded.mapped('uuid')
        granted = set(Validation.search([
            ('config_id', '=', config.id), ('success', '=', True), ('reference', 'in', references),
        ]).mapped('action'))
        partner = self.partner_id
        preset = self.preset_id if 'preset_id' in self._fields else False
        used = {
            'payment': self.state in ('paid', 'done', 'invoiced') and bool(self.payment_ids),
            'discount': any(line.discount for line in self.lines if not line.refunded_orderline_id)
            or self._mv_has_global_discount(),
            'pricelist': bool(self.pricelist_id) and self.pricelist_id not in (
                config.pricelist_id | partner.property_product_pricelist | (preset.pricelist_id if preset else self.env['product.pricelist'])),
            'fiscal_position': bool(self.fiscal_position_id) and self.fiscal_position_id not in (
                config.default_fiscal_position_id | partner.property_account_position_id
                | (preset.fiscal_position_id if preset else self.env['account.fiscal.position'])),
            'refund': bool(refunded),
        }
        return [action for action in actions if used[action] and action not in granted]

    def _mv_has_global_discount(self):
        product = self.config_id.discount_product_id if 'discount_product_id' in self.config_id._fields else False
        return bool(product) and product in self.lines.product_id

    def _mv_flag(self):
        for order in self:
            keys = ','.join(order._mv_unvalidated_actions()) or False
            if keys != (order.mv_unvalidated or False):
                order.write({'mv_unvalidated': keys, 'mv_flagged': bool(keys)})

    def action_mv_mark_checked(self):
        self.write({'mv_flagged': False})


class PosMakePayment(models.TransientModel):
    _inherit = 'pos.make.payment'

    def _mv_order(self):
        return self.env['pos.order'].browse(self.env.context.get('active_id', False)).exists()

    def check(self):
        order = self._mv_order()
        if order:
            self.env['pos.manager.validation']._mv_require(order.config_id, 'payment', order._mv_reference())
        return super().check()

    def action_mv_check(self):
        self.ensure_one()
        order = self._mv_order()
        if order and order.config_id._mv_guards('payment') and not self.env.su:
            return self.env['pos.manager.validation.wizard']._open_for(
                order.config_id, 'payment', self, 'check', order=order)
        return self.check()
