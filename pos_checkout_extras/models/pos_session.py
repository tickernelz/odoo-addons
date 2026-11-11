from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _create_account_move(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        data = super()._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)
        self._stamp_counter_analytic(self.move_id.line_ids)
        return data

    def _get_diff_vals(self, payment_method_id, diff_amount, outstanding_account=False):
        result = super()._get_diff_vals(payment_method_id, diff_amount, outstanding_account)
        config = self.config_id
        if result and config.analytic_distribution:
            for vals in result:
                account = self.env["account.account"].browse(vals["account_id"])
                if config._analytic_applies_to_account(account):
                    vals["analytic_distribution"] = config.analytic_distribution
        return result

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        result = super()._validate_session(balancing_account, amount_to_balance, bank_payment_method_diffs)
        if self.state == "closed" and self.config_id.analytic_distribution:
            self._stamp_counter_analytic(self._counter_posted_lines())
        return result

    def _counter_posted_lines(self):
        payments = self.env["account.payment"].sudo().search([("pos_session_id", "in", self.ids)])
        return self.sudo().statement_line_ids.move_id.line_ids | payments.move_id.line_ids

    def _stamp_counter_analytic(self, lines):
        self.ensure_one()
        config = self.config_id
        distribution = config.analytic_distribution
        if not distribution:
            return 0
        targets = lines.sudo().filtered_domain(config._analytic_scope_domain()).filtered(
            lambda line: line.analytic_distribution != distribution
        )
        if targets:
            targets.write({"analytic_distribution": distribution})
        return len(targets)

    def _counter_move_ids(self):
        sessions = self.sudo()
        moves = sessions.move_id | sessions.statement_line_ids.move_id
        moves |= self.env["account.payment"].sudo().search([("pos_session_id", "in", sessions.ids)]).move_id
        split_journals = sessions.payment_method_ids.filtered(
            lambda method: method.type == "bank" and method.split_transactions
        ).journal_id
        if split_journals:
            ref_domain = [("ref", "=like", "%%(%s)" % session.name) for session in sessions]
            moves |= self.env["account.move"].sudo().search(
                [("journal_id", "in", split_journals.ids), ("move_type", "=", "entry")]
                + ["|"] * (len(ref_domain) - 1) + ref_domain
            )
        return moves.ids

    def _counter_line_domain(self):
        return [
            ("company_id", "in", self.config_id.company_id.ids),
            ("parent_state", "=", "posted"),
            "|",
            ("move_id", "in", self._counter_move_ids()),
            "&",
            ("move_id.pos_order_ids.session_id", "in", self.ids),
            ("display_type", "=", "product"),
        ]
