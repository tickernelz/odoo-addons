from datetime import datetime, time, timedelta, timezone

import pytz

from odoo import api, fields, models
from odoo.tools import format_amount, format_date


class PosBankCardDashboard(models.AbstractModel):
    _name = 'pos.bank.card.dashboard'
    _description = 'Bank Card Programme Dashboard'

    @api.model
    def _utc_bounds(self, date_from, date_to):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        start = tz.localize(datetime.combine(date_from, time.min))
        end = tz.localize(datetime.combine(date_to + timedelta(days=1), time.min))
        return (
            start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))

    @api.model
    def get_dashboard(self, date_from=False, date_to=False, bank_id=False, program_id=False):
        Usage = self.env['pos.bank.card.usage']
        Program = self.env['pos.bank.card.program']
        Usage.check_access('read')
        company = self.env.company
        currency = company.currency_id
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(date_from) or today.replace(day=1)
        date_to = fields.Date.to_date(date_to) or today
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        start, end = self._utc_bounds(date_from, date_to)
        scope = [('company_id', '=', company.id)]
        if bank_id:
            scope.append(('bank_id', '=', bank_id))
        if program_id:
            scope.append(('program_id', '=', program_id))
        domain = scope + [('state', '=', 'confirmed'), ('date', '>=', start), ('date', '<', end)]

        def money(value):
            return format_amount(self.env, value or 0.0, currency)

        [(count, basis, discount, bank_share, store_share)] = Usage._read_group(
            domain, [], ['__count', 'basis_amount:sum', 'discount_amount:sum',
                         'bank_share_amount:sum', 'store_share_amount:sum'])
        [(unclaimed_count, unclaimed)] = Usage._read_group(
            domain + [('claim_state', '=', 'not_claimed')], [], ['__count', 'bank_share_amount:sum'])
        kpis = [
            {'key': 'usage', 'label': self.env._('Card Transactions'), 'value': str(count),
             'hint': self.env._('Basis %s', money(basis))},
            {'key': 'discount', 'label': self.env._('Discount Given'), 'value': money(discount),
             'hint': self.env._('Bank %(bank)s, store %(store)s', bank=money(bank_share), store=money(store_share))},
            {'key': 'bank_share', 'label': self.env._('Bank Share'), 'value': money(bank_share),
             'hint': self.env._('Not claimed yet: %(amount)s (%(count)s)', count=unclaimed_count, amount=money(unclaimed))},
        ]
        Claim = self.env['pos.bank.card.claim']
        if Claim.has_access('read'):
            claim_domain = [('company_id', '=', company.id), ('state', 'in', ('draft', 'sent'))]
            if bank_id:
                claim_domain.append(('bank_id', '=', bank_id))
            [(claim_count, claim_amount)] = Claim._read_group(claim_domain, [], ['__count', 'total_amount:sum'])
            kpis.append({
                'key': 'claims', 'label': self.env._('Claims Awaiting Payment'), 'value': money(claim_amount),
                'hint': self.env._('Open claims: %s', claim_count)})

        daily_rows = dict(
            (day.date() if isinstance(day, datetime) else day, (disc, share))
            for day, disc, share in Usage._read_group(
                domain, ['date:day'], ['discount_amount:sum', 'bank_share_amount:sum']))
        days = []
        span = (date_to - date_from).days
        for offset in range(min(span, 366) + 1):
            day = date_from + timedelta(days=offset)
            disc, share = daily_rows.get(day, (0.0, 0.0))
            days.append({'label': format_date(self.env, day, date_format='d MMM'),
                         'discount': disc or 0.0, 'bank_share': share or 0.0})

        outlets = []
        for config, outlet_count, disc, share in Usage._read_group(
                domain, ['config_id'], ['__count', 'discount_amount:sum', 'bank_share_amount:sum'],
                order='bank_share_amount:sum desc', limit=10):
            outlets.append({
                'name': config.sudo().display_name if config else self.env._('No outlet'),
                'count': outlet_count, 'discount': money(disc), 'bank_share': money(share),
                'share_percent': round(100.0 * share / bank_share, 1) if bank_share else 0.0})

        quotas = []
        quota_domain = [('company_id', '=', company.id), ('state', '=', 'active'), ('quota_type', '!=', 'none')]
        if bank_id:
            quota_domain.append(('bank_id', '=', bank_id))
        if program_id:
            quota_domain.append(('id', '=', program_id))
        for program in Program.search(quota_domain):
            bucket = program._quota_for(today)
            if program.quota_type == 'count':
                limit, used = program.quota_limit_count, bucket.used_count if bucket else 0
                used_label, limit_label = str(used), str(limit)
            else:
                limit, used = program.quota_limit_amount, bucket.used_amount if bucket else 0.0
                used_label, limit_label = money(used), money(limit)
            if program.quota_period == 'program':
                period = program.validity_label
            else:
                start_day, end_day = program._quota_bucket_dates(today)
                period = '%s - %s' % (format_date(self.env, start_day), format_date(self.env, end_day))
            quotas.append({
                'id': program.id, 'name': program.name, 'bank': program.bank_id.display_name,
                'used': used_label, 'limit': limit_label,
                'percent': min(round(100.0 * used / limit, 1), 100.0) if limit else 0.0,
                'period': period})

        all_programs = Program.search([('company_id', '=', company.id)])
        return {
            'date_from': fields.Date.to_string(date_from),
            'date_to': fields.Date.to_string(date_to),
            'currency': currency.symbol,
            'kpis': kpis,
            'days': days,
            'outlets': outlets,
            'quotas': quotas,
            'banks': [{'id': bank.id, 'name': bank.display_name} for bank in all_programs.bank_id],
            'programs': [{'id': program.id, 'name': program.name, 'bank_id': program.bank_id.id}
                         for program in all_programs],
        }
