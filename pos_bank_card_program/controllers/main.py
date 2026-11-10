from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class PosBankCardLanguage(http.Controller):

    def _installed_languages(self):
        return request.env['res.lang'].get_installed()

    def _check_pos_session(self):
        session = request.env['pos.session'].search([
            ('user_id', '=', request.env.uid),
            ('state', 'in', ['opening_control', 'opened', 'closing_control']),
        ], limit=1)
        if not session:
            raise UserError(request.env._(
                'The language can only be changed from an open point of sale session.'))

    @http.route('/pos_bank_card_program/lang/list', type='jsonrpc', auth='user')
    def language_list(self):
        self._check_pos_session()
        return {
            'current': request.env.user.lang,
            'langs': [
                {'code': code, 'name': name}
                for code, name in self._installed_languages()
            ],
        }

    @http.route('/pos_bank_card_program/lang/set', type='jsonrpc', auth='user')
    def language_set(self, code):
        self._check_pos_session()
        if code not in {lang_code for lang_code, _name in self._installed_languages()}:
            raise UserError(request.env._(
                'The language %s is not installed.', code))
        request.env.user.write({'lang': code})
        request.session.context = dict(request.session.context, lang=code)
        return code
