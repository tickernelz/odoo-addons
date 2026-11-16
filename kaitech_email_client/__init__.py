# -*- coding: utf-8 -*-
from . import models
from . import controllers


def uninstall_hook(env):
    """Odoo keeps system parameters and attachments after uninstall: remove ours."""
    env["ir.config_parameter"].sudo().search([("key", "=like", "kaitech_email_client.%")]).unlink()
    env["ir.attachment"].sudo().search([("res_model", "in", ("umc.message", "umc.composer"))]).unlink()
