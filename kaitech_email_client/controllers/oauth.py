# -*- coding: utf-8 -*-
import logging

from werkzeug.utils import redirect

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..models import oauth_providers as oauth

_logger = logging.getLogger(__name__)


class UmcOAuthController(http.Controller):

    @http.route("/umc/oauth/callback", type="http", auth="user", methods=["GET"], csrf=False)
    def oauth_callback(self, state=None, code=None, error=None, error_description=None, **kw):
        """Google / Microsoft send the user back here after consent."""
        Mailbox = request.env["umc.mailbox"]
        try:
            box, uid = Mailbox._from_oauth_state(state)
            if not box or uid != request.env.uid:
                raise AccessError("state/user mismatch")
            box._check_configure()
        except AccessError as exc:
            _logger.warning("Email Client OAuth callback rejected: %s", exc)
            return request.make_response(str(exc), status=403)

        if error or not code:
            box.sudo().write({"connection_state": "error", "last_error": (error_description or error or "")[:250]})
        else:
            try:
                box._store_tokens(oauth.exchange_code(request.env, box.account_type, code))
            except oauth.OAuthError as exc:
                box.sudo().write({"connection_state": "error", "last_error": str(exc)[:250]})
        return redirect("/web#model=umc.mailbox&id=%s&view_type=form" % box.id, code=303)
