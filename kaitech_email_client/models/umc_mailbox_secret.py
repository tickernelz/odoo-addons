# -*- coding: utf-8 -*-
from odoo import fields, models


class UmcMailboxSecret(models.Model):
    """Credentials of a mailbox, kept out of the mailbox record.

    Only reachable with sudo() from the mailbox code: no menu, no view, and the
    access rights give it to administrators only. Team members can read and
    answer a shared mailbox without ever being able to read its password or
    OAuth tokens over RPC.
    """

    _name = "umc.mailbox.secret"
    _description = "Email Client for Odoo Full - mailbox credentials"
    _rec_name = "mailbox_id"

    mailbox_id = fields.Many2one("umc.mailbox", required=True, ondelete="cascade", index=True)
    password = fields.Char()
    access_token = fields.Char()
    refresh_token = fields.Char()
    token_expires_at = fields.Integer(help="Unix time")

    def init(self):
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS umc_mailbox_secret_mailbox_uniq "
                            "ON umc_mailbox_secret (mailbox_id)")
