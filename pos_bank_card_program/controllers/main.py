from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request


class PosBankCardLanguage(http.Controller):

    def _installed_languages(self):
        return request.env['res.lang'].get_installed()

    def _check_pos_session(self, session_id):
        session = request.env['pos.session']
        if isinstance(session_id, int) and not isinstance(session_id, bool):
            try:
                session = session.browse(session_id).exists()
                open_state = session.state in (
                    'opening_control', 'opened', 'closing_control')
            except AccessError:
                open_state = False
        else:
            open_state = False
        if not session or not open_state:
            raise UserError(request.env._(
                'The language can only be changed from an open point of sale session.'))

    @http.route('/pos_bank_card_program/lang/set', type='jsonrpc', auth='user')
    def language_set(self, code, session_id=None):
        self._check_pos_session(session_id)
        if not isinstance(code, str) or code not in {
                lang_code for lang_code, _name in self._installed_languages()}:
            raise UserError(request.env._(
                'The language %s is not installed.', code))
        request.env.user.write({'lang': code})
        request.session.context = dict(request.session.context, lang=code)
        return code
