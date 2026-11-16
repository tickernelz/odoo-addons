from odoo import fields, models

P = "kaitech_email_client."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    umc_google_client_id = fields.Char("Google client ID", config_parameter=P + "google_client_id")
    umc_google_client_secret = fields.Char("Google client secret", config_parameter=P + "google_client_secret")
    umc_microsoft_client_id = fields.Char("Microsoft application (client) ID",
                                          config_parameter=P + "microsoft_client_id")
    umc_microsoft_client_secret = fields.Char("Microsoft client secret", config_parameter=P + "microsoft_client_secret")
    umc_microsoft_tenant = fields.Char("Microsoft tenant", config_parameter=P + "microsoft_tenant",
                                       help="'common' for any account, 'organizations' for work accounts only, "
                                            "or your tenant ID.")
    umc_oauth_redirect_uri = fields.Char("Redirect URI", compute="_compute_umc_oauth_redirect_uri",
                                         help="Register this exact URL in Google Cloud and in Microsoft Entra.")

    def _compute_umc_oauth_redirect_uri(self):
        for rec in self:
            rec.umc_oauth_redirect_uri = rec.get_base_url().rstrip("/") + "/umc/oauth/callback"
