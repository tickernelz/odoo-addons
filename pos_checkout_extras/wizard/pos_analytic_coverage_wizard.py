from odoo import fields, models


class PosAnalyticCoverageWizard(models.TransientModel):
    _name = "pos.analytic.coverage.wizard"
    _description = "POS Analytic Coverage"

    date_from = fields.Date(
        string="From",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(string="To", required=True, default=fields.Date.context_today)
    config_ids = fields.Many2many(
        "pos.config",
        string="Points of Sale",
        help="Leave empty to include every point of sale.",
    )

    def action_print(self):
        return self.env.ref("pos_checkout_extras.action_report_pos_analytic_coverage").report_action(self)

    def _coverage(self):
        self.ensure_one()
        rows = []
        for config in self.config_ids or self.env["pos.config"].search([]):
            sessions = config._closed_sessions(self.date_from, self.date_to)
            counts = config._analytic_coverage(sessions)
            rows.append({
                "name": config.name,
                "distribution": config._analytic_distribution_label(),
                "scope": dict(config._fields["analytic_scope"]._description_selection(self.env))[config.analytic_scope],
                "sessions": len(sessions),
                **counts,
                "coverage": self._percent(counts["stamped"], counts["items"]),
            })
        totals = {key: sum(row[key] for row in rows) for key in ("sessions", "items", "stamped", "missing", "other")}
        totals["coverage"] = self._percent(totals["stamped"], totals["items"])
        return rows, totals

    def _percent(self, part, whole):
        return round(100.0 * part / whole) if whole else 0
