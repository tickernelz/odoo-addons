from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    _inherit = "pos.order"

    payment_reference = fields.Char(
        string="Payment Reference",
        size=64,
        copy=False,
        index="btree_not_null",
        help="Reference given by the cashier at payment, for example a transfer number or an approval code.",
    )

    @api.model
    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        self.browse(order_id)._check_customer_required()
        return order_id

    def _check_customer_required(self):
        for order in self:
            config = order.config_id
            if (
                config.customer_required_mode != "off"
                and not order.partner_id
                and order.state in ("paid", "done")
                and config.customer_required_since
                and order.date_order >= config.customer_required_since
                and not order.is_refund
                and not order.lines.refunded_orderline_id
            ):
                raise ValidationError(_(
                    "Order %(order)s has no customer. %(pos)s requires a customer on every paid order.",
                    order=order.pos_reference or order.name,
                    pos=config.name,
                ))

    def _get_invoice_lines_values(self, line_values, pos_line, move_type):
        values = super()._get_invoice_lines_values(line_values, pos_line, move_type)
        distribution = self.session_id.config_id.analytic_distribution
        if distribution:
            values["analytic_distribution"] = distribution
        return values
