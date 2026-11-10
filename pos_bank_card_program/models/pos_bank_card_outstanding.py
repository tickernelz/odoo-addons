from odoo import fields, models, tools


class PosBankCardOutstanding(models.Model):
    _name = 'pos.bank.card.outstanding'
    _description = 'Bank Card Outstanding per Bank'
    _auto = False
    _order = 'bank_id'

    bank_id = fields.Many2one('res.partner', string='Bank', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    usage_count = fields.Integer(string='Usage Lines', readonly=True)
    total_amount = fields.Monetary(
        string='Outstanding', currency_field='currency_id', readonly=True)
    amount_0_30 = fields.Monetary(
        string='0-30 Days', currency_field='currency_id', readonly=True)
    amount_31_60 = fields.Monetary(
        string='31-60 Days', currency_field='currency_id', readonly=True)
    amount_over_60 = fields.Monetary(
        string='Over 60 Days', currency_field='currency_id', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    row_number() OVER (ORDER BY p.bank_id, u.company_id) AS id,
                    p.bank_id AS bank_id,
                    u.company_id AS company_id,
                    c.currency_id AS currency_id,
                    count(*) AS usage_count,
                    sum(u.bank_share_amount) AS total_amount,
                    sum(CASE WHEN CURRENT_DATE - u.date::date <= 30
                        THEN u.bank_share_amount ELSE 0 END) AS amount_0_30,
                    sum(CASE WHEN CURRENT_DATE - u.date::date BETWEEN 31 AND 60
                        THEN u.bank_share_amount ELSE 0 END) AS amount_31_60,
                    sum(CASE WHEN CURRENT_DATE - u.date::date > 60
                        THEN u.bank_share_amount ELSE 0 END) AS amount_over_60
                FROM pos_bank_card_usage u
                JOIN pos_bank_card_program p ON p.id = u.program_id
                JOIN res_company c ON c.id = u.company_id
                WHERE u.state = 'confirmed'
                    AND u.claim_state IN ('not_claimed', 'claimed')
                GROUP BY p.bank_id, u.company_id, c.currency_id
            )
        """ % self._table)
