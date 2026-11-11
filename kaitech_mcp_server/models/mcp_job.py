import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


_GOVERNANCE_FIELDS = frozenset({
    "operation", "user_id", "token_id", "session_id", "payload", "mcp_scope", "mcp_restrictions",
})


class McpJob(models.Model):
    _name = "mcp.job"
    _description = "MCP Async Job"
    _inherit = ["mail.thread"]
    _order = "create_date desc"
    _rec_name = "name"

    name = fields.Char(
        string="Name",
        compute="_compute_name",
        store=True,
    )
    operation = fields.Selection(
        [
            ("bulk_update", "Bulk Update"),
            ("bulk_create", "Bulk Create"),
            ("bulk_unlink", "Bulk Delete"),
            ("export_csv", "Export CSV"),
            ("export_xlsx", "Export XLSX"),
            ("call_method", "Call Method"),
            ("custom", "Custom"),
        ],
        string="Operation",
        required=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    token_id = fields.Many2one(
        "mcp.token",
        string="Token",
        readonly=True,
        ondelete="set null",
    )
    session_id = fields.Many2one(
        "mcp.session",
        string="Session",
        readonly=True,
        ondelete="set null",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="State",
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )
    progress = fields.Integer(
        string="Progress (%)",
        default=0,
    )
    total_records = fields.Integer(string="Total Records", default=0)
    processed_records = fields.Integer(string="Processed Records", default=0)
    payload = fields.Text(
        string="Payload (JSON)",
        help="Input arguments for the job, including mcp_scope and mcp_restrictions snapshots.",
    )
    result = fields.Text(
        string="Result (JSON)",
        help="Job output or summary (PII redacted).",
    )
    error_message = fields.Text(string="Error Message")
    started_at = fields.Datetime(string="Started At", readonly=True)
    finished_at = fields.Datetime(string="Finished At", readonly=True)
    duration_ms = fields.Integer(
        string="Duration (ms)",
        compute="_compute_duration_ms",
        store=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Export File",
        ondelete="set null",
        readonly=True,
    )
    # Governance snapshots — populated at submit time
    mcp_scope = fields.Char(
        string="MCP Scope",
        readonly=True,
        help="Snapshot of the token scope at job submission time.",
    )
    mcp_restrictions = fields.Text(
        string="MCP Restrictions (JSON)",
        readonly=True,
        help="Snapshot of the token restrictions at job submission time.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._privileged():
            raise AccessError(_("MCP jobs are submitted through the odoo_submit_job tool."))
        return super().create(vals_list)

    def write(self, vals):
        if not self._privileged() and _GOVERNANCE_FIELDS & set(vals):
            raise AccessError(_("The owner, operation, payload and scope of an MCP job cannot be changed."))
        return super().write(vals)

    def _privileged(self):
        return self.env.su or self.env.user._is_system()

    @api.depends("operation")
    def _compute_name(self):
        op_labels = dict(self._fields["operation"].selection)
        for rec in self:
            label = op_labels.get(rec.operation, rec.operation or "")
            job_id = rec._origin.id or rec.id
            rec.name = f"Job #{job_id} — {label}" if job_id else f"New Job — {label}"

    @api.depends("started_at", "finished_at")
    def _compute_duration_ms(self):
        for rec in self:
            if rec.started_at and rec.finished_at:
                delta = rec.finished_at - rec.started_at
                rec.duration_ms = int(delta.total_seconds() * 1000)
            else:
                rec.duration_ms = 0

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def action_run(self):
        """Synchronously execute this job. Idempotent — refuses to run if state != pending."""
        self.ensure_one()
        if self.state != "pending":
            raise UserError(
                _("Cannot run job %s: current state is '%s' (must be 'pending').") % (
                    self.name, self.state
                )
            )
        self._set_running()
        try:
            result = self._execute()
            self._set_done(result)
        except Exception as exc:
            _logger.exception("MCP job %s failed", self.id)
            self._set_failed(str(exc))

    def action_cancel(self):
        """Cancel a pending job."""
        self.ensure_one()
        if self.state != "pending":
            raise UserError(
                _("Cannot cancel job %s: only pending jobs can be cancelled.") % self.name
            )
        self.write({"state": "cancelled"})

    def action_retry(self):
        """Reset a failed job to pending so the cron picks it up again."""
        self.ensure_one()
        if self.state != "failed":
            raise UserError(
                _("Cannot retry job %s: only failed jobs can be retried.") % self.name
            )
        self.write({"state": "pending", "error_message": False, "progress": 0})

    def action_open_attachment(self):
        """Return an act_url action to download the job's export attachment."""
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_("No attachment available for this job."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=1",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    def _set_running(self):
        self.write({
            "state": "running",
            "started_at": fields.Datetime.now(),
            "progress": 0,
        })

    def _set_done(self, result: dict):
        from ..services.redaction import redact
        from ..services.json_utils import odoo_json_default
        try:
            safe_result = json.dumps(redact(result), ensure_ascii=False, default=odoo_json_default)
        except Exception:
            safe_result = str(result)
        self.write({
            "state": "done",
            "finished_at": fields.Datetime.now(),
            "progress": 100,
            "result": safe_result,
            "error_message": False,
        })

    def _set_failed(self, error_message: str):
        self.write({
            "state": "failed",
            "finished_at": fields.Datetime.now(),
            "error_message": error_message,
        })

    # ------------------------------------------------------------------
    # Core execution dispatcher
    # ------------------------------------------------------------------

    def _execute(self) -> dict:
        """Dispatch to the appropriate runner. Restores governance context before executing."""
        self.ensure_one()

        # Parse payload
        try:
            payload = json.loads(self.payload or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}

        args = payload.get("args") or {}

        # Restore scope + restrictions snapshots into env.context — security-critical
        ctx_updates = {
            "mcp_scope": self.mcp_scope or payload.get("mcp_scope") or "write",
        }
        raw_restrictions = self.mcp_restrictions or payload.get("mcp_restrictions_json")
        if raw_restrictions:
            try:
                ctx_updates["mcp_restrictions"] = json.loads(raw_restrictions)
            except (json.JSONDecodeError, TypeError):
                pass

        owner = self.user_id
        job_env = self.with_user(owner).env(context={**self.env.context, **ctx_updates})
        if ctx_updates["mcp_scope"] == "admin" and owner._is_system():
            job_env = job_env(su=True)

        from ..services import job_runner

        op = self.operation
        if op == "bulk_update":
            return job_runner.run_bulk_update(job_env, args, self)
        if op == "bulk_create":
            return job_runner.run_bulk_create(job_env, args, self)
        if op == "bulk_unlink":
            return job_runner.run_bulk_unlink(job_env, args, self)
        if op == "export_csv":
            return job_runner.run_export_csv(job_env, args, self)
        if op == "export_xlsx":
            return job_runner.run_export_xlsx(job_env, args, self)
        if op in ("call_method", "custom"):
            return job_runner.run_custom(job_env, args, self)

        raise ValueError(f"Unknown operation type: {op!r}")

    # ------------------------------------------------------------------
    # Cron entry point
    # ------------------------------------------------------------------

    @api.model
    def _process_pending_jobs(self, limit: int = 10):
        """Pick up to *limit* pending jobs and execute each in isolation.

        Called by the ir.cron record. Partial failure in one job does not
        prevent remaining jobs from running.

        Note: SAVEPOINT is intentionally avoided — PGBouncer in transaction
        pooling mode does not support server-side SAVEPOINT commands.
        action_run() already handles its own exception isolation internally.
        """
        pending = self.search([("state", "=", "pending")], order="id asc", limit=limit)
        for job in pending:
            try:
                job.action_run()
            except Exception as exc:
                _logger.exception("MCP cron: job %s raised outside action_run", job.id)
                try:
                    job.sudo().write({"state": "failed", "error_message": str(exc)})
                except Exception:
                    _logger.exception("MCP cron: could not fail job %s", job.id)
