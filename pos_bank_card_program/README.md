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
Manager group of the same company. PINs are stored hashed (PBKDF2), verified
on the server, and five failed attempts lock verification for the calling
user for five minutes; three wrong entries in the dialog return to the
payment screen. Removal releases the reserved quota. Deleting the card
payment line or the whole order also releases the discount and its
reservation, and closing a session releases reservations that were never
paid.

Validation guards: an order whose bank card discount line has no usage row
cannot be paid, and an empty Approval Code is rejected with a message naming
the field when the programme requires one. Paying the order confirms the
usage row. Refunding a discounted order cancels its usage row so it is never
claimed; the quota is returned only when the programme enables "Refund
Returns Quota".

Offline: discounts are refused while offline by default in the dialog. A
programme with "Allow Offline" applies the discount from local data up to
its per-outlet daily allowance. Sync never refuses a sale that already
happened: when the order syncs, the server re-checks the permission, the
allowance for the order day, and the shared quota. An order that passes is
recorded as a claimable offline usage; an order that does not (no
permission, allowance used up, quota lost, approval code missing) is still
stored as paid, but its usage row is marked as needing review with a bank
share of zero and a cancelled claim state, so the store bears the discount
and the bank is never charged for it.

Accounting: the journal assumption is that POS prices are tax-included, so
the discount line and the bank share already contain tax. When a session
closes, one reclassification entry per programme moves the bank share from
the programme discount account to the bank claim receivable account with the
bank as partner (debit receivable, credit discount). The discount order line
itself posts to the programme discount account at closing, so after the
reclassification that account keeps exactly the store share. With 50:50
funding only the bank half is reclassified; the store half stays promotion
cost. Refunding an order whose share was already reclassified posts a
reversal entry for that usage, in the same or a later session. The entry
uses the programme reclassification journal when set. Programmes without both
accounts are skipped, and each usage row records its entry so a share is
never reclassified twice.
Language:

- When more than one language is installed, the point of sale menu shows a
  Language item under Reload Data, with the current language next to it. The
  dialog lists the languages installed on the server; picking one writes the
  user language and the session context through
  /pos_bank_card_program/lang/set, then reloads the page. The route rejects a
  language that is not installed and a user without an open point of sale
  session, and it can only ever change the caller's own language.
- The open order is kept across the reload. Product names on order lines
  that were already added do not change language; only lines added after the
  switch use the new language.
- Language is a per-user setting, also outside the point of sale. Cashiers
  who take turns on one shared account share one language, in the register
  and in the back office alike.

Updating the translation catalogue:

1. Export a fresh template from a database where the module is installed:
   ADDONS_PATH="<repo>,<odoo addons>" sh ci/export-pot.sh <db> pos_bank_card_program > /tmp/fresh.pot
2. Copy /tmp/fresh.pot over i18n/pos_bank_card_program.pot, then merge it
   into i18n/id.po with msgmerge --no-fuzzy-matching, fill every empty
   entry, and drop obsolete ones with msgattrib --no-obsolete.
3. Check the result: python3 ci/i18n.py check and
   msgfmt --check -o /dev/null pos_bank_card_program/i18n/id.po must both
   pass, and python3 ci/i18n.py compare <folder holding the fresh .pot>
   must report no difference. English is the source language; Indonesian
   text lives only in i18n/id.po.

Known limit of this foundation stage: release_quota and confirm_quota address
a usage row by id only. Binding them to the caller's order and session arrives
with the cashier dialog stage that first wires them to an order.

Access: Programme Manager maintains programmes and BIN ranges, Finance owns
claims, and point of sale users only read programmes, usage, and quota rows.
The reservation, release, and confirm calls perform their writes on those
three models under sudo after the eligibility and locking checks, so a
cashier cannot adjust quota counters directly through the ORM.

## Claims

Finance bills a bank from Point of Sale > Bank Cards > Claim to Bank. The
wizard takes a bank, an optional programme restriction, and a period
(monthly by default), previews the bank share per outlet, then creates one
claim over the confirmed usage that is not claimed yet. Cancelled usage
never joins a claim. A bank can only have one live claim per period and
programme scope: an overlapping claim is refused until the first one is
cancelled or recorded as rejected, which releases its usage lines back to
not claimed. The claim menus also live under Accounting > Customers, so a
Finance user reaches them without point of sale groups; creating the claim
invoice and reconciling it additionally needs Invoicing/Billing rights
(account.group_account_invoice), because both act on account.move.

On the claim, Export Excel writes one sheet per programme using that
programme's claim columns (sequence, value, and header are configurable per
programme; a generic template is installed by default). Each sheet ends with
a total row, and the bank share column adds up to the claim total. Create
Invoice raises one customer invoice to the bank partner with one line per
programme for its bank share, on the Bank Card Programme Claim product; an
income account must be set on that product or its category. Mark Sent,
then either Reconcile (the claim becomes paid once its invoice is paid) or,
when the bank paid without an invoice, Mark Paid. Paid usage lines leave
the outstanding balance.

## Reports

Under Point of Sale > Reporting, all with native list, pivot, and graph
views: Bank Card Usage (filter by bank, programme, outlet, status, and
claim status), Programme Dashboard (discount and bank share per day),
Outstanding per Bank (confirmed bank share not yet paid, aged 0-30, 31-60,
and over 60 days by usage date), Card Transaction Details, Usage per
Outlet, and Programme Quota Usage.

Icon: the source is static/description/icon.svg; regenerate the PNG with
inkscape static/description/icon.svg --export-type=png --export-filename=static/description/icon.png -w 256 -h 256
(run from the module directory).
