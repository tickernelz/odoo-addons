from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    kai_promo_id = fields.Many2one('kai.pos.promo', string='POS Promotion', index='btree_not_null', ondelete='restrict')
    kai_promo_shares = fields.Text(
        string='Promotion Basis',
        help='Share of each counted line in the promotion basis, used to refund the discount proportionally.')
    kai_promo_refund_of_id = fields.Many2one(
        'pos.order.line', string='Refunded Promotion Line', index='btree_not_null', ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('kai_promo_refund_of_id') and not vals.get('kai_promo_id'):
                vals['kai_promo_id'] = self.browse(vals['kai_promo_refund_of_id']).kai_promo_id.id
        return super().create(vals_list)

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if not fields_list:
            return fields_list
        return fields_list + ['kai_promo_id', 'kai_promo_shares', 'kai_promo_refund_of_id']
