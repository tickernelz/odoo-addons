from odoo import fields, models


class ReportPosOrder(models.Model):
    _inherit = "report.pos.order"

    payment_reference = fields.Char(string="Payment Reference", readonly=True)

    def _select(self):
        return super()._select() + ", s.payment_reference AS payment_reference"
