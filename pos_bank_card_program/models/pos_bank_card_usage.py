from odoo import api, fields, models


class PosBankCardUsage(models.Model):
    _name = 'pos.bank.card.usage'
    _description = 'Bank Card Programme Usage'
    _order = 'date desc, id desc'

    program_id = fields.Many2one(
        'pos.bank.card.program', string='Programme', required=True, ondelete='restrict', index=True)
    bin_range_id = fields.Many2one('pos.bank.card.bin.range', string='BIN Range', ondelete='set null')
    company_id = fields.Many2one(
        'res.company', string='Company', related='program_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', related='program_id.currency_id', readonly=True)
    date = fields.Datetime(string='Date', required=True, default=fields.Datetime.now, index=True)
    pos_order_id = fields.Many2one('pos.order', string='POS Order', ondelete='set null', index=True)
    payment_id = fields.Many2one('pos.payment', string='POS Payment', ondelete='set null')
    session_id = fields.Many2one(
        'pos.session', string='Session', related='pos_order_id.session_id', store=True,
        readonly=True, index=True)
    config_id = fields.Many2one('pos.config', string='Outlet')
    quota_id = fields.Many2one('pos.bank.card.quota', string='Quota Bucket', ondelete='set null')
    cardlimit_id = fields.Many2one('pos.bank.card.cardlimit', string='Daily Card Counter', ondelete='set null')
    card_bin = fields.Char(string='Card BIN', required=True, index=True)
    card_last4 = fields.Char(string='Last 4 Digits')
    masked_card = fields.Char(string='Card', compute='_compute_masked_card', store=True)
    approval_code = fields.Char(string='Approval Code')
    card_brand = fields.Char(string='Network')
    card_type = fields.Char(string='Card Type')
    card_tier = fields.Char(string='Tier')
    basis_amount = fields.Monetary(string='Basis', currency_field='currency_id', default=0.0)
    discount_amount = fields.Monetary(string='Discount', currency_field='currency_id', default=0.0)
    bank_share_amount = fields.Monetary(string='Bank Share', currency_field='currency_id', default=0.0)
    store_share_amount = fields.Monetary(string='Store Share', currency_field='currency_id', default=0.0)
    state = fields.Selection(
        selection=[
            ('reserved', 'Reserved'),
            ('confirmed', 'Confirmed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', default='confirmed', required=True, index=True)
    claim_state = fields.Selection(
        selection=[
            ('not_claimed', 'Not Claimed'),
            ('claimed', 'Claimed'),
            ('paid', 'Paid'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Claim Status', default='not_claimed', required=True, index=True)
    claim_id = fields.Many2one('pos.bank.card.claim', string='Claim', ondelete='set null', index=True)
    reclass_move_id = fields.Many2one('account.move', string='Reclassification Entry', ondelete='set null')
    reclass_reversal_move_id = fields.Many2one(
        'account.move', string='Reclassification Reversal', ondelete='set null')
    offline = fields.Boolean(string='Offline', default=False)

    @api.depends('card_bin', 'card_last4')
    def _compute_masked_card(self):
        for usage in self:
            card_bin = usage.card_bin or ''
            if len(card_bin) >= 8:
                usage.masked_card = '%s %s **** %s' % (
                    card_bin[:4], card_bin[4:8], usage.card_last4 or '')
            else:
                usage.masked_card = '%s %s** **** %s' % (
                    card_bin[:4], card_bin[4:6], usage.card_last4 or '')

    @api.depends('program_id', 'masked_card')
    def _compute_display_name(self):
        for usage in self:
            usage.display_name = '%s %s' % (usage.program_id.name or '', usage.masked_card or '')

    def release_reservation(self):
        self.ensure_one()
        if self.state != 'reserved':
            return {'released': False, 'reason': 'not_reserved'}
        program = self.program_id
        program._lock_rows(self.quota_id, self.cardlimit_id)
        if self.quota_id:
            self.quota_id.sudo().write({
                'used_count': max(self.quota_id.used_count - 1, 0),
                'used_amount': max(self.quota_id.used_amount - self.discount_amount, 0.0),
            })
        if self.cardlimit_id:
            self.cardlimit_id.sudo().write({'used_count': max(self.cardlimit_id.used_count - 1, 0)})
        self.sudo().write({'state': 'cancelled', 'claim_state': 'cancelled'})
        return {'released': True, 'usage_id': self.id}

    def confirm_reservation(self):
        self.ensure_one()
        if self.state != 'reserved':
            return {'confirmed': False, 'reason': 'not_reserved'}
        self.sudo().write({'state': 'confirmed'})
        return {'confirmed': True, 'usage_id': self.id}

    def _reverse_reclassification(self):
        self.ensure_one()
        move = self.reclass_move_id
        if not move or move.state != 'posted' or self.reclass_reversal_move_id:
            return
        program = self.program_id
        amount = self.bank_share_amount
        if not amount or not (program.receivable_account_id and program.discount_account_id):
            return
        name = self.env._('Bank card claim reclassification reversal %s', program.name)
        reversal = self.env['account.move'].sudo().create({
            'journal_id': move.journal_id.id,
            'date': fields.Date.context_today(self),
            'ref': move.ref or move.name,
            'company_id': move.company_id.id,
            'line_ids': [
                (0, 0, {
                    'name': name,
                    'account_id': program.discount_account_id.id,
                    'debit': amount,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': name,
                    'account_id': program.receivable_account_id.id,
                    'partner_id': program.bank_id.id,
                    'debit': 0.0,
                    'credit': amount,
                }),
            ],
        })
        reversal.action_post()
        self.sudo().write({'reclass_reversal_move_id': reversal.id})

    def cancel_for_refund(self):
        self.ensure_one()
        if self.state == 'cancelled':
            return {'cancelled': False, 'reason': 'already_cancelled'}
        program = self.program_id
        if program.refund_returns_quota:
            program._lock_rows(self.quota_id, self.cardlimit_id)
            if self.quota_id:
                self.quota_id.sudo().write({
                    'used_count': max(self.quota_id.used_count - 1, 0),
                    'used_amount': max(self.quota_id.used_amount - self.discount_amount, 0.0),
                })
            if self.cardlimit_id:
                self.cardlimit_id.sudo().write({'used_count': max(self.cardlimit_id.used_count - 1, 0)})
        self._reverse_reclassification()
        self.sudo().write({'state': 'cancelled', 'claim_state': 'cancelled'})
        if self.pos_order_id:
            self.pos_order_id.write({'bank_card_reservation_state': 'released'})
        payment = self.payment_id or (self.pos_order_id.payment_ids.filtered(lambda item: item.bank_card_program_id)[:1] if self.pos_order_id else self.env['pos.payment'])
        if payment:
            payment.sudo().write({'bank_card_claim_state': 'cancelled'})
        return {'cancelled': True, 'usage_id': self.id}
