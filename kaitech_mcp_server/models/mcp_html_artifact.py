# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
MCP HTML Artifact model.

An artifact is a free-form interactive HTML snippet (HTML + optional CSS + JS)
produced by an AI client and served through a strictly sandboxed iframe. Unlike
mcp.portal.page, the content is opaque to the server: no spec, no widgets, just
raw markup that the client wants to render to a viewer.

Security model
--------------
* Content is wrapped in an iframe with ``sandbox="allow-scripts"`` (no
  same-origin), so script inside the artifact cannot read the parent page,
  cookies, or storage.
* A strict Content-Security-Policy is injected as a ``<meta http-equiv>`` tag
  inside the iframe srcdoc. ``connect-src`` defaults to ``'none'``, blocking
  any outbound network calls from the artifact.
* Only a small allowlist of CDNs is permitted for ``script-src`` and
  ``style-src`` (jsDelivr, unpkg, cdnjs, Google Fonts).
"""

import logging
import re
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_HTML_BYTES = 512 * 1024  # 512 KiB hard cap per artifact field


def _slugify(text: str) -> str:
    try:
        from odoo.tools import slugify as _odoo_slugify
        return _odoo_slugify(text, allow_slash=False)
    except (ImportError, TypeError):
        pass
    lowered = (text or "").lower().strip()
    return _SLUG_RE.sub("-", lowered).strip("-") or "artifact"


class McpHtmlArtifact(models.Model):
    _name = "mcp.html.artifact"
    _description = "MCP HTML Artifact"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(string="Title", required=True, tracking=True)
    slug = fields.Char(
        string="Slug",
        required=True,
        index=True,
        copy=False,
        help="URL path component: /mcp-artifact/<slug>. Auto-generated from title on creation.",
    )
    description = fields.Text(string="Description")
    enabled = fields.Boolean(string="Enabled", default=True, tracking=True)

    is_public = fields.Boolean(
        string="Public",
        default=False,
        tracking=True,
        help="If enabled, anyone can view the artifact without logging in. "
             "Otherwise an active Odoo session or a valid ?token= parameter is required.",
    )
    access_token = fields.Char(
        string="Embed Token",
        readonly=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(24),
        help="Append ?token=<value> to the artifact URL to grant read access without a login.",
    )
    created_by = fields.Many2one(
        "res.users",
        string="Created By",
        default=lambda self: self.env.uid,
        ondelete="restrict",
        index=True,
    )

    theme = fields.Selection(
        [("light", "Light"), ("dark", "Dark"), ("auto", "Auto")],
        string="Theme",
        default="light",
    )

    html = fields.Text(
        string="HTML Body",
        required=True,
        default="<h1>Hello, world!</h1>",
        help="HTML markup placed inside the <body> of the sandboxed document. "
             "May contain inline <style> and <script> tags.",
    )
    css = fields.Text(
        string="CSS",
        help="Optional CSS inserted into a <style> tag in <head>.",
    )
    js = fields.Text(
        string="JavaScript",
        help="Optional JS inserted into a <script> tag at the end of <body>. "
             "Runs inside the sandbox; no access to parent window.",
    )

    view_count = fields.Integer(string="Views", default=0, readonly=True)
    last_viewed = fields.Datetime(string="Last Viewed", readonly=True)

    artifact_url = fields.Char(string="Artifact URL", compute="_compute_urls")
    embed_url = fields.Char(string="Embed URL", compute="_compute_urls")

    _slug_unique = models.Constraint(
        "UNIQUE(slug)",
        "An HTML artifact with this slug already exists. Choose a different slug.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._unique_slug(vals["name"])
            if not vals.get("access_token"):
                vals["access_token"] = secrets.token_urlsafe(24)
            self._check_created_by(vals)
            self._validate_size(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._check_created_by(vals)
        self._validate_size(vals)
        return super().write(vals)

    def _check_created_by(self, vals):
        """Pages render with their owner's access rights: only Settings administrators may hand one to someone else."""
        if "created_by" in vals and vals["created_by"] != self.env.uid \
                and not (self.env.su or self.env.user._is_system()):
            raise AccessError(_("Only Settings administrators can change the owner of %(model)s.",
                                model=self._description))

    @api.depends("slug", "access_token")
    def _compute_urls(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for rec in self:
            rec.artifact_url = f"{base}/mcp-artifact/{rec.slug}" if rec.slug else ""
            rec.embed_url = (
                f"{base}/mcp-artifact/{rec.slug}?token={rec.access_token}"
                if rec.slug and rec.access_token
                else ""
            )

    def action_preview(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return {
            "type": "ir.actions.act_url",
            "url": f"{base}/mcp-artifact/{self.slug}",
            "target": "new",
        }

    def action_regenerate_token(self):
        for rec in self:
            rec.access_token = secrets.token_urlsafe(24)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Token Regenerated"),
                "message": _(
                    "The embed token has been regenerated. "
                    "Previous embed links are now invalid."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _validate_size(self, vals: dict) -> None:
        for fname in ("html", "css", "js"):
            val = vals.get(fname)
            if val and len(val.encode("utf-8")) > _MAX_HTML_BYTES:
                raise UserError(
                    _("Field %s exceeds the maximum size of %d KiB.")
                    % (fname, _MAX_HTML_BYTES // 1024)
                )

    def _unique_slug(self, name: str) -> str:
        base = _slugify(name)
        candidate = base
        suffix = 1
        while self.search_count([("slug", "=", candidate)]):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate
