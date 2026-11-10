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

Since the cashier stage, release_quota and confirm_quota also check the
outlet: when the caller passes its pos.config id, a usage row reserved at a
different outlet is refused with reason wrong_outlet.

Cashier flow: a payment method flagged "Requires Bank Card Info" opens the
bank card dialog after its payment line is added. The cashier enters the card
prefix (6-8 digits), the last 4 digits, and the EDC approval code. Detection
(network, type, tier, programme, eligibility) runs locally from the data
loaded at session opening; applying the discount makes the single server call
that reserves quota. Losing the quota race shows one refusal reason in the
dialog and the sale continues without the discount. The discount is a
negative order line on the programme discount product (the module ships
"Bank Card Discount"); the card context (programme, masked card, reservation
state) is stored on order and payment fields, so it survives a page reload
and a language switch. Removing the discount uses the "Remove Bank Card
Discount" button on the payment screen and requires the Store Manager PIN:
the bank_card_manager_pin field of a user in the Programme Manager or POS
Manager group, verified on the server; three wrong entries return to the
payment screen. Removal releases the reserved quota.

Validation guards: an order whose bank card discount line has no usage row
cannot be paid, and an empty Approval Code is rejected with a message naming
the field when the programme requires one. Paying the order confirms the
usage row. Refunding a discounted order cancels its usage row so it is never
claimed; the quota is returned only when the programme enables "Refund
Returns Quota".

Offline: discounts are refused while offline by default. A programme with
"Allow Offline" applies the discount from local data up to its per-outlet
allowance of offline orders, and the usage row is created when the order
syncs.

Accounting: the journal assumption is that POS prices are tax-included, so
the discount line and the bank share already contain tax. When a session
closes, one reclassification entry per programme moves the bank share from
the programme discount account to the bank claim receivable account with the
bank as partner (debit receivable, credit discount). With 50:50 funding only
the bank half is reclassified; the store half stays promotion cost. The entry
uses the programme reclassification journal when set. Programmes without both
accounts are skipped, and each usage row records its entry so a share is
never reclassified twice.

Access: Programme Manager maintains programmes and BIN ranges, Finance owns
claims, and point of sale users only read programmes, usage, and quota rows.
The reservation, release, and confirm calls perform their writes on those
three models under sudo after the eligibility and locking checks, so a
cashier cannot adjust quota counters directly through the ORM.
