from odoo import api, models


class ReportPosAnalyticCoverage(models.AbstractModel):
    _name = "report.pos_checkout_extras.report_pos_analytic_coverage"
    _description = "POS Analytic Coverage Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["pos.analytic.coverage.wizard"].browse(docids)
        return {
            "doc_ids": docids,
            "doc_model": "pos.analytic.coverage.wizard",
            "docs": docs,
            "coverage": {doc.id: doc._coverage() for doc in docs},
        }
