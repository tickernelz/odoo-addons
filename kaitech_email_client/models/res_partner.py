# -*- coding: utf-8 -*-
from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    umc_thread_count = fields.Integer("Emails", compute="_compute_umc_thread_count")

    def _compute_umc_thread_count(self):
        Thread = self.env["umc.thread"]
        if not Thread.has_access("read"):
            self.umc_thread_count = 0
            return
        groups = Thread._read_group([("partner_id", "in", self.ids)], ["partner_id"], ["__count"])
        counts = {partner.id: count for partner, count in groups}
        for partner in self:
            partner.umc_thread_count = counts.get(partner.id, 0)

    def action_umc_open_emails(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client", "tag": "umc.inbox", "name": _("Emails"),
            "context": {"umc_partner_id": self.id, "umc_partner_name": self.display_name},
        }
