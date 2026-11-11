from odoo import api, fields, models
from odoo.addons.base.models.res_partner import _tz_get


class PosConfig(models.Model):
    _inherit = 'pos.config'

    kai_timezone = fields.Selection(
        _tz_get, string='Promotion Timezone',
        default=lambda self: self.env.company.partner_id.tz or self.env.user.tz or 'UTC',
        help='Happy hours of this point of sale follow this timezone, not the timezone of the cashier.')

    @api.model
    def _get_special_products(self):
        promos = self.env['kai.pos.promo'].sudo().search([('state', 'in', ('approved', 'active'))])
        default = self.env.ref('kai_pos_promotion.product_kai_promo_discount', raise_if_not_found=False)
        return super()._get_special_products() | promos.discount_product_id | (default or self.env['product.product'])
