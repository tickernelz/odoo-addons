import base64
import io
import json
import logging
import os
import shutil
import zipfile

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.module import get_module_path
from odoo.tools import config

_logger = logging.getLogger(__name__)


class McpGeneratedModule(models.Model):
    _name = "mcp.generated.module"
    _description = "MCP Generated Module"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------

    name = fields.Char(
        string="Module Title",
        required=True,
        tracking=True,
    )
    technical_name = fields.Char(
        string="Technical Name",
        required=True,
        index=True,
        help="Python/folder name of the module (snake_case).",
    )
    summary = fields.Char(string="Summary")
    description = fields.Text(string="Description")
    category = fields.Char(string="Category", default="Productivity")
    author = fields.Char(string="Author", default="MCP Generator")
    version = fields.Char(string="Version", default="19.0.1.0.0")
    license = fields.Selection(
        [("OPL-1", "OPL-1"), ("LGPL-3", "LGPL-3"), ("AGPL-3", "AGPL-3")],
        string="License",
        default="LGPL-3",
    )
    depends = fields.Char(
        string="Depends",
        default="base",
        help="Comma-separated list of Odoo module technical names.",
    )
    spec = fields.Text(
        string="Spec (JSON)",
        required=True,
        help="Full JSON spec used to generate this module.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("generated", "Generated"),
            ("installed", "Installed"),
            ("failed", "Failed"),
        ],
        default="draft",
        tracking=True,
        required=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="ZIP Archive",
        readonly=True,
        ondelete="set null",
    )
    generation_log = fields.Text(
        string="Generation Log",
        readonly=True,
        help="Warnings and errors captured during ZIP generation.",
    )
    created_by = fields.Many2one(
        "res.users",
        string="Created By",
        default=lambda self: self.env.uid,
        readonly=True,
        ondelete="set null",
    )
    installed_at = fields.Datetime(
        string="Installed At",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _technical_name_unique = models.Constraint(
        "UNIQUE(technical_name)",
        "A module with this technical name already exists.",
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @api.model
    def validate_spec(self, spec_dict: dict) -> list:
        """Return list of validation error strings (empty = valid)."""
        from ..services import module_generator
        return module_generator.validate_spec(spec_dict)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_generate(self):
        """Validate spec, build ZIP, store as ir.attachment, set state=generated."""
        self.ensure_one()
        from ..services import module_generator

        try:
            spec_dict = json.loads(self.spec or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            self.write({
                "state": "failed",
                "generation_log": f"Invalid JSON in spec field: {exc}",
            })
            raise UserError(_(
                "Spec field contains invalid JSON: %(error)s",
                error=str(exc),
            ))

        try:
            zip_bytes, warnings = module_generator.build_zip(spec_dict)
        except ValueError as exc:
            log_msg = str(exc)
            self.write({
                "state": "failed",
                "generation_log": log_msg,
            })
            raise UserError(_(
                "Module generation failed:\n%(error)s",
                error=log_msg,
            ))
        except Exception as exc:
            log_msg = f"Unexpected error: {exc}"
            self.write({
                "state": "failed",
                "generation_log": log_msg,
            })
            _logger.exception("Unexpected error generating module %s", self.technical_name)
            raise UserError(_(
                "Module generation failed with unexpected error:\n%(error)s",
                error=str(exc),
            ))

        # Remove old attachment if any
        if self.attachment_id:
            self.attachment_id.sudo().unlink()

        zip_b64 = base64.b64encode(zip_bytes).decode()
        attachment = self.env["ir.attachment"].sudo().create({
            "name": f"{self.technical_name}.zip",
            "datas": zip_b64,
            "mimetype": "application/zip",
            "res_model": self._name,
            "res_id": self.id,
        })

        log_text = "\n".join(warnings) if warnings else "No warnings."
        self.write({
            "state": "generated",
            "attachment_id": attachment.id,
            "generation_log": log_text,
        })
        self.message_post(
            body=_(
                "Module ZIP generated successfully. "
                "%(warning_count)s warning(s). "
                "Attachment: %(filename)s",
                warning_count=len(warnings),
                filename=attachment.name,
            )
        )
        return True

    def action_download(self):
        """Return ir.actions.act_url to download the generated ZIP."""
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_("No ZIP archive available. Generate the module first."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=1",
            "target": "self",
        }

    def action_install(self):
        """Extract ZIP to configured addons path and install via Odoo module manager.

        Requires mcp_generated_modules_path in the Odoo configuration file.
        Path must exist, be writable, and must NOT be inside community/ or enterprise/.
        """
        self.ensure_one()
        if self.state != "generated":
            raise UserError(
                _("Module must be in 'Generated' state before installing. Current state: %(state)s",
                  state=self.state)
            )
        if not self.attachment_id:
            raise UserError(_("No ZIP archive found. Generate the module first."))

        base_path = (config.get("mcp_generated_modules_path") or "").strip()
        if not base_path:
            raise UserError(_(
                "Generated modules path is not configured. The server administrator sets "
                "mcp_generated_modules_path in the Odoo configuration file."
            ))

        base_path = os.path.realpath(base_path)
        self._validate_install_path(base_path)

        target_dir = os.path.join(base_path, self.technical_name)
        existing = get_module_path(self.technical_name, display_warning=False)
        if existing and os.path.dirname(os.path.realpath(existing)) != base_path:
            raise UserError(_(
                "A module named %(name)s already exists at %(path)s. Choose another technical name.",
                name=self.technical_name,
                path=existing,
            ))

        # Fetch ZIP bytes
        att = self.attachment_id.sudo()
        zip_bytes = base64.b64decode(att.datas)

        # Atomic: remove existing dir if present, extract fresh
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zf.extractall(base_path)
        except Exception as exc:
            # Rollback: remove partially extracted directory
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            raise UserError(_(
                "Failed to extract ZIP archive: %(error)s",
                error=str(exc),
            ))

        # Update module list
        try:
            self.env["ir.module.module"].sudo().update_list()
        except Exception as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise UserError(_(
                "Failed to update module list after extraction: %(error)s",
                error=str(exc),
            ))

        # Find and install
        module_rec = self.env["ir.module.module"].sudo().search(
            [("name", "=", self.technical_name)], limit=1
        )
        if not module_rec:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise UserError(_(
                "Module %(name)s not found in the module list after update_list. "
                "Check that the addons path is registered in Odoo configuration.",
                name=self.technical_name,
            ))

        try:
            module_rec.button_immediate_install()
        except Exception as exc:
            raise UserError(_(
                "Module installation failed: %(error)s",
                error=str(exc),
            ))

        self.write({
            "state": "installed",
            "installed_at": fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "Module installed successfully at %(path)s.",
                path=target_dir,
            )
        )
        return True

    def action_reset_to_draft(self):
        """Reset to draft (only from generated or failed). Clears attachment."""
        for rec in self:
            if rec.state not in ("generated", "failed"):
                raise UserError(_(
                    "Cannot reset module %(name)s: current state is %(state)s.",
                    name=rec.name,
                    state=rec.state,
                ))
        attachments = self.mapped("attachment_id")
        self.write({
            "state": "draft",
            "attachment_id": False,
            "generation_log": False,
        })
        if attachments:
            attachments.sudo().unlink()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_install_path(self, path: str) -> None:
        """Raise UserError if *path* is not safe for module extraction."""
        if not os.path.isdir(path):
            raise UserError(_(
                "Generated modules path %(path)s does not exist or is not a directory.",
                path=path,
            ))
        if not os.access(path, os.W_OK):
            raise UserError(_(
                "Generated modules path %(path)s is not writable by the Odoo process.",
                path=path,
            ))

        # Refuse paths inside Odoo upstream directories
        _BLOCKED_SEGMENTS = ("community", "enterprise")
        norm = path.lower().replace("\\", "/")
        for seg in _BLOCKED_SEGMENTS:
            if f"/{seg}/" in norm or norm.endswith(f"/{seg}"):
                raise UserError(_(
                    "Installation path %(path)s appears to be inside the Odoo %(seg)s "
                    "directory. Choose a custom addons path instead.",
                    path=path,
                    seg=seg,
                ))
