# POS Bank Card Programmes

Bank card programmes for the Odoo 19 point of sale. A programme defines the
bank, the validity period, the outlets and payment methods where it applies,
the BIN ranges that identify eligible cards, the discount (percent, basis,
minimum, maximum, promotion order, bank share), a shared quota, and a per-card
daily limit. The full card number is never stored: only the BIN, the last four
digits, and the EDC approval code.

Programmes and BIN ranges are loaded into the point of sale when a session
opens, so card detection runs locally. Reserving quota is the only step that
calls the server; the quota row is locked with SELECT ... FOR UPDATE so two
outlets cannot take the last slot twice.

Server API used by the point of sale:

- pos.bank.card.program.detect_card(card_bin, card_last4, basis_amount,
  config_id, payment_method_id, online) returns eligibility or one refusal
  reason: tier_excluded, below_minimum, quota_exhausted, card_used_today,
  bin_not_registered, offline.
- pos.bank.card.program.compute_discount(basis_amount) on a programme record
  returns the discount and the bank and store shares.
- pos.bank.card.program.reserve_quota(program_id, card_bin, card_last4,
  basis_amount, approval_code, config_id, payment_method_id, pos_order_id,
  payment_id) locks the
  quota and daily card counter, creates a reserved usage row, and returns it.
- pos.bank.card.program.release_quota(usage_id) cancels a reserved usage row
  and returns its quota.
- pos.bank.card.program.confirm_quota(usage_id) confirms a reserved usage row.

Known limit of this foundation stage: release_quota and confirm_quota address
a usage row by id only. Binding them to the caller's order and session arrives
with the cashier dialog stage that first wires them to an order.

Access: Programme Manager maintains programmes and BIN ranges, Finance owns
claims, and point of sale users only read programmes, usage, and quota rows.
The reservation, release, and confirm calls perform their writes on those
three models under sudo after the eligibility and locking checks, so a
cashier cannot adjust quota counters directly through the ORM.
