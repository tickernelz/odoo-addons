from odoo import fields, models


class PosPayment(models.Model):
    _inherit = "pos.payment"

    payment_reference = fields.Char(
        related="pos_order_id.payment_reference",
        store=True,
        index="btree_not_null",
    )
