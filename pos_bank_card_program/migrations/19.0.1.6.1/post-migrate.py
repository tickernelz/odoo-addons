import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    method = env.ref('pos_bank_card_program.payment_method_card_bank', raise_if_not_found=False)
    if not method or method.journal_id:
        return
    if method.open_session_ids:
        _logger.warning(
            'Demo payment method %s has no journal; close sessions %s and set a bank journal on it.',
            method.display_name, ', '.join(method.open_session_ids.mapped('name')))
        return
    journal = env['account.journal'].search(
        [('type', '=', 'bank'), ('company_id', '=', method.company_id.id)], limit=1)
    if journal:
        method.journal_id = journal
