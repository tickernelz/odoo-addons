from odoo import Command, fields
from odoo.tests import TransactionCase


def tier_promo(env, name, tiers, **values):
    today = fields.Date.context_today(env['kai.pos.promo'])
    return env['kai.pos.promo'].create({
        'name': name,
        'date_start': today,
        'date_end': fields.Date.add(today, days=30),
        'tier_ids': [Command.create({'threshold': threshold, 'discount_type': kind, 'value': value})
                     for threshold, kind, value in tiers],
        **values,
    })


def activate(promos):
    promos.action_submit()
    promos.sudo().action_approve()
    return promos


class PromoCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seminyak = tier_promo(cls.env, 'Seminyak Tier', [
            (300000, 'percent', 10), (500000, 'percent', 15), (1000000, 'percent', 20)])
