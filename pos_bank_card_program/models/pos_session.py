from odoo import models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _load_pos_data_models(self, config):
        return super()._load_pos_data_models(config) + [
            'pos.bank.card.program', 'pos.bank.card.bin.range',
        ]
