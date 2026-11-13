import json
from collections import defaultdict
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL, float_round

PROFIT_AND_LOSS_GROUPS = ("income", "expense")
NON_ACCOUNTING_LINES = ("line_section", "line_subsection", "line_note")


class PosConfig(models.Model):
    _name = "pos.config"
    _inherit = ["pos.config", "analytic.mixin"]

    customer_required_mode = fields.Selection(
        [
            ("off", "Not required"),
            ("order", "Required at order"),
            ("payment", "Required at payment"),
        ],
        string="Customer Required",
        default="off",
        required=True,
    )
    customer_required_since = fields.Datetime(
        string="Customer Required Since",
        readonly=True,
        copy=False,
        help="Orders dated from this moment are refused by the server when they are paid without a customer.",
    )
    allow_payment_reference = fields.Boolean(string="Payment Reference")
    analytic_scope = fields.Selection(
        [("pnl", "Profit and loss lines"), ("all", "All lines")],
        string="Apply To",
        default="pnl",
        required=True,
    )
    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        "pos_config_analytic_account_rel",
        "config_id",
        "account_id",
        string="Analytic Accounts",
        check_company=True,
        context={"active_test": False},
        help="Accounts in different plans each take the whole amount. Accounts in the same plan share it, "
        "evenly unless the split is adjusted.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            if vals.get("customer_required_mode", "off") != "off":
                vals["customer_required_since"] = now
            self._follow_distribution_accounts(vals)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("customer_required_mode", "off") != "off":
            switched_on = self.filtered(lambda config: config.customer_required_mode == "off")
            if switched_on:
                super(PosConfig, switched_on).write({"customer_required_since": fields.Datetime.now()})
        self._follow_distribution_accounts(vals)
        if "analytic_distribution" in vals or "analytic_account_ids" in vals:
            old_distributions = {config.id: config.analytic_distribution for config in self}
            result = super().write(vals)
            self._log_distribution_change(old_distributions)
            return result
        return super().write(vals)

    def _log_distribution_change(self, old_distributions):
        changed = self.filtered(lambda config: config.analytic_distribution != old_distributions[config.id])
        self.env["pos.analytic.sync.log"].sudo().create([
            {
                "config_id": config.id,
                "kind": "distribution",
                "old_distribution": old_distributions[config.id],
                "new_distribution": config.analytic_distribution,
            }
            for config in changed
        ])

    @api.model
    def _follow_distribution_accounts(self, vals):
        if "analytic_distribution" in vals and "analytic_account_ids" not in vals:
            vals["analytic_account_ids"] = [Command.set(self._distribution_accounts(vals["analytic_distribution"]).ids)]

    @api.model
    def _distribution_shares(self, distribution):
        shares = defaultdict(float)
        for key, percent in (distribution or {}).items():
            if key == "__update__":
                continue
            for account_id in key.split(","):
                if account_id.isdigit():
                    shares[int(account_id)] += percent
        return shares

    @api.model
    def _distribution_accounts(self, distribution):
        return self.env["account.analytic.account"].browse(self._distribution_shares(distribution)).exists()

    @api.model
    def _distribution_for_accounts(self, accounts, distribution):
        accounts = accounts._origin
        shares = self._distribution_shares(distribution)
        if set(shares) == set(accounts.ids):
            return distribution or False
        precision = self.env["decimal.precision"].precision_get("Percentage Analytic")
        previous = self._distribution_accounts(distribution).grouped("root_plan_id")
        result = {}
        for plan, plan_accounts in accounts.grouped("root_plan_id").items():
            if previous.get(plan) == plan_accounts:
                result.update((str(account.id), shares[account.id]) for account in plan_accounts)
                continue
            even = float_round(100.0 / len(plan_accounts), precision_digits=precision)
            for account in plan_accounts[:-1]:
                result[str(account.id)] = even
            result[str(plan_accounts[-1].id)] = float_round(100.0 - even * (len(plan_accounts) - 1), precision_digits=precision)
        return result or False

    @api.depends("analytic_account_ids")
    def _compute_analytic_distribution(self):
        for config in self:
            config.analytic_distribution = self._distribution_for_accounts(
                config.analytic_account_ids, config.analytic_distribution)

    @api.onchange("analytic_distribution")
    def _onchange_analytic_distribution_accounts(self):
        for config in self:
            config.analytic_account_ids = self._distribution_accounts(config.analytic_distribution)

    @api.constrains("analytic_distribution", "analytic_account_ids", "company_id")
    def _check_analytic_distribution(self):
        for config in self:
            if self._distribution_accounts(config.analytic_distribution) != config.analytic_account_ids:
                raise ValidationError(_(
                    "The analytic split of %(pos)s must use exactly its analytic accounts.", pos=config.name))
            if config.analytic_distribution:
                config.with_context(validate_analytic=True)._validate_distribution(
                    company_id=config.company_id.id,
                    business_domain="general",
                )

    def _analytic_applies_to_account(self, account):
        self.ensure_one()
        return self.analytic_scope == "all" or account.internal_group in PROFIT_AND_LOSS_GROUPS

    def _analytic_scope_domain(self):
        self.ensure_one()
        domain = [("display_type", "not in", NON_ACCOUNTING_LINES)]
        if self.analytic_scope == "pnl":
            domain.append(("account_id.internal_group", "in", PROFIT_AND_LOSS_GROUPS))
        return domain

    def _closed_sessions(self, date_from, date_to):
        self.ensure_one()
        return self.env["pos.session"].search([
            ("config_id", "=", self.id),
            ("state", "=", "closed"),
            ("stop_at", ">=", fields.Datetime.to_datetime(date_from)),
            ("stop_at", "<", fields.Datetime.to_datetime(date_to) + timedelta(days=1)),
        ])

    def _analytic_line_query(self, sessions, order=None):
        self.ensure_one()
        lines = self.env["account.move.line"].sudo()
        lines.flush_model(["analytic_distribution"])
        return lines._search(
            sessions._counter_line_domain() + self._analytic_scope_domain(),
            order=order,
        )

    def _analytic_coverage(self, sessions):
        self.ensure_one()
        counts = {"items": 0, "stamped": 0, "missing": 0, "other": 0}
        if not sessions:
            return counts
        query = self._analytic_line_query(sessions)
        column = SQL.identifier(query.table, "analytic_distribution")
        distribution = json.dumps(self.analytic_distribution) if self.analytic_distribution else None
        items, stamped, missing = self.env.execute_query(query.select(
            SQL("COUNT(*)"),
            SQL("COUNT(*) FILTER (WHERE %s = %s::jsonb)", column, distribution),
            SQL("COUNT(*) FILTER (WHERE %s IS NULL OR %s = '{}'::jsonb)", column, column),
        ))[0]
        counts.update(items=items, stamped=stamped, missing=missing, other=items - stamped - missing)
        return counts

    def _analytic_pending_line_ids(self, sessions, mode, limit=None):
        self.ensure_one()
        if not sessions or not self.analytic_distribution:
            return []
        query = self._analytic_line_query(sessions, order="id")
        column = SQL.identifier(query.table, "analytic_distribution")
        if mode == "missing":
            query.add_where(SQL("(%s IS NULL OR %s = '{}'::jsonb)", column, column))
        else:
            query.add_where(SQL("%s IS DISTINCT FROM %s::jsonb", column, json.dumps(self.analytic_distribution)))
        query.limit = limit
        return [row[0] for row in self.env.execute_query(query.select(SQL.identifier(query.table, "id")))]

    def _analytic_distribution_label(self):
        self.ensure_one()
        parts = []
        for key, percent in (self.analytic_distribution or {}).items():
            accounts = self.env["account.analytic.account"].browse(int(account_id) for account_id in key.split(","))
            parts.append("%s %s%%" % (" + ".join(accounts.mapped("name")), round(percent, 2)))
        return " / ".join(parts)
