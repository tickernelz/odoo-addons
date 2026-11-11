import time

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import split_every


class PosAnalyticSyncWizard(models.TransientModel):
    _name = "pos.analytic.sync.wizard"
    _description = "Synchronise POS Analytic Distribution"

    _batch_size = 10000
    _chunk_size = 1000

    config_id = fields.Many2one(
        "pos.config",
        string="Point of Sale",
        help="Leave empty to synchronise every point of sale that has an analytic distribution.",
    )
    date_from = fields.Date(
        string="From",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(string="To", required=True, default=fields.Date.context_today)
    mode = fields.Selection(
        [
            ("missing", "Only items without a distribution"),
            ("replace", "Replace the distribution of every item"),
        ],
        default="missing",
        required=True,
    )
    distribution_label = fields.Char(string="Distribution", compute="_compute_preview")
    session_count = fields.Integer(string="Sessions", compute="_compute_preview")
    matched_count = fields.Integer(string="Journal items", compute="_compute_preview")
    pending_count = fields.Integer(string="To update", compute="_compute_preview")
    stamped_count = fields.Integer(string="Already stamped", compute="_compute_preview")
    overwrite_count = fields.Integer(string="Other distribution replaced", compute="_compute_preview")
    state = fields.Selection([("preview", "Preview"), ("done", "Done")], default="preview")
    updated_count = fields.Integer(string="Updated", readonly=True)
    skipped_count = fields.Integer(string="Skipped", readonly=True)
    duration = fields.Float(string="Seconds", readonly=True, digits=(16, 1))

    @api.model
    def action_open(self, config):
        return self.create({"config_id": config.id})._reopen()

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Synchronise Analytic Distribution"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _configs(self):
        configs = self.config_id or self.env["pos.config"].search([])
        return configs.filtered("analytic_distribution")

    @api.depends("config_id", "date_from", "date_to", "mode")
    def _compute_preview(self):
        for wizard in self:
            values = dict.fromkeys(
                ("session_count", "matched_count", "pending_count", "stamped_count", "overwrite_count"), 0
            )
            configs = wizard._configs() if wizard.date_from and wizard.date_to else self.env["pos.config"]
            for config in configs:
                sessions = config._closed_sessions(wizard.date_from, wizard.date_to)
                coverage = config._analytic_coverage(sessions)
                values["session_count"] += len(sessions)
                values["matched_count"] += coverage["items"]
                values["stamped_count"] += coverage["stamped"]
                if wizard.mode == "missing":
                    values["pending_count"] += coverage["missing"]
                else:
                    values["pending_count"] += coverage["items"] - coverage["stamped"]
                    values["overwrite_count"] += coverage["other"]
            if wizard.config_id:
                values["distribution_label"] = wizard.config_id._analytic_distribution_label()
            else:
                values["distribution_label"] = self.env._("Each point of sale's own distribution")
            wizard.update(values)

    def action_synchronise(self):
        self.ensure_one()
        start = time.monotonic()
        budget = self._batch_size
        updated = skipped = 0
        for config in self._configs():
            if budget <= 0:
                break
            sessions = config._closed_sessions(self.date_from, self.date_to)
            line_ids = config._analytic_pending_line_ids(sessions, self.mode, limit=budget)
            if not line_ids:
                continue
            budget -= len(line_ids)
            config_updated, config_skipped = self._write_distribution(config, line_ids)
            updated += config_updated
            skipped += config_skipped
            self.env["pos.analytic.sync.log"].sudo().create({
                "config_id": config.id,
                "kind": "sync",
                "date_from": self.date_from,
                "date_to": self.date_to,
                "mode": self.mode,
                "updated_count": config_updated,
                "skipped_count": config_skipped,
                "new_distribution": config.analytic_distribution,
            })
        self.write({
            "state": "done",
            "updated_count": updated,
            "skipped_count": skipped,
            "duration": time.monotonic() - start,
        })
        return self._reopen()

    def _write_distribution(self, config, line_ids):
        lines_model = self.env["account.move.line"].sudo()
        distribution = config.analytic_distribution
        updated = skipped = 0
        for chunk in split_every(self._chunk_size, line_ids):
            lines = lines_model.browse(chunk)
            try:
                with self.env.cr.savepoint():
                    lines.write({"analytic_distribution": distribution})
                updated += len(lines)
            except (UserError, ValidationError):
                for line in lines:
                    try:
                        with self.env.cr.savepoint():
                            line.write({"analytic_distribution": distribution})
                        updated += 1
                    except (UserError, ValidationError):
                        skipped += 1
            self.env.invalidate_all()
        return updated, skipped

    def action_back(self):
        self.write({"state": "preview"})
        return self._reopen()
