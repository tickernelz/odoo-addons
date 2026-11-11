from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_mv_employee_ids = fields.Many2many(related='pos_config_id.mv_employee_ids', readonly=False)
    pos_mv_close_session = fields.Boolean(related='pos_config_id.mv_close_session', readonly=False)
    pos_mv_delete_order = fields.Boolean(related='pos_config_id.mv_delete_order', readonly=False)
    pos_mv_delete_line = fields.Boolean(related='pos_config_id.mv_delete_line', readonly=False)
    pos_mv_payment = fields.Boolean(related='pos_config_id.mv_payment', readonly=False)
    pos_mv_discount = fields.Boolean(related='pos_config_id.mv_discount', readonly=False)
    pos_mv_price = fields.Boolean(related='pos_config_id.mv_price', readonly=False)
    pos_mv_decrease_qty = fields.Boolean(related='pos_config_id.mv_decrease_qty', readonly=False)
    pos_mv_cash_move = fields.Boolean(related='pos_config_id.mv_cash_move', readonly=False)
    pos_mv_create_product = fields.Boolean(related='pos_config_id.mv_create_product', readonly=False)
    pos_mv_fiscal_position = fields.Boolean(related='pos_config_id.mv_fiscal_position', readonly=False)
    pos_mv_pricelist = fields.Boolean(related='pos_config_id.mv_pricelist', readonly=False)
    pos_mv_refund = fields.Boolean(related='pos_config_id.mv_refund', readonly=False)
