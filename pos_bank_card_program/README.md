# POS Bank Card Programmes

Bank card programmes for the Odoo 19 point of sale. A programme defines the
bank, the validity period, the outlets and payment methods where it applies,
the BIN ranges that identify eligible cards, the discount (a percentage with
a maximum, or a fixed amount per transaction in the company currency; basis,
minimum, promotion order, bank share), a shared quota, and a per-card
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
  returns the discount and the bank and store shares. A percentage programme
  applies its percent and maximum; a fixed amount programme returns the fixed
  amount, never more than the basis, rounded to the programme currency. The
  point of sale computes the same amounts locally from the loaded programme.
- pos.bank.card.program.reserve_quota(program_id, card_bin, card_last4,
  basis_amount, approval_code, config_id, payment_method_id, pos_order_id,
  payment_id) locks the
  quota and daily card counter, creates a reserved usage row, and returns it.
- pos.bank.card.program.release_quota(usage_id) cancels a reserved usage row
  and returns its quota.
- pos.bank.card.program.confirm_quota(usage_id) confirms a reserved usage row.

Since the cashier stage, release_quota and confirm_quota also check the
outlet: a usage row reserved at an outlet can only be released or confirmed
with that same pos.config id; any other value, including none, is refused
with reason wrong_outlet.

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

Access: Programme Manager maintains programmes and BIN ranges, Finance owns
claims, and point of sale users only read programmes, usage, and quota rows.
The administrator receives both groups when the module is installed, and
groups for other users are assigned manually.
The reservation, release, and confirm calls perform their writes on those
three models under sudo after the eligibility and locking checks, so a
cashier cannot adjust quota counters directly through the ORM.

## Models

- pos.bank.card.program: a bank programme. Bank, validity dates, outlets
  (pos.config) and payment methods it applies to, mode, discount type
  (percentage or fixed amount), discount percent or fixed discount amount,
  discount basis, minimum basis, maximum discount (percentage programmes),
  promotion order, bank share
  percent, excluded products and categories, quota (type, period, limit),
  per-card daily limit, offline setting, and the discount product, discount
  account, claim receivable account, and reclassification journal.
- pos.bank.card.bin.range: one BIN range of a programme (6 or 8 digits,
  ranges of one programme may not overlap), with network, card type, tier,
  eligibility, and the refusal reason shown for an excluded tier.
- pos.bank.card.quota: one quota bucket per programme and period (unique),
  holding the used transaction count and amount.
- pos.bank.card.cardlimit: one daily counter per programme, card (BIN plus
  last four digits), and day (unique).
- pos.bank.card.usage: one row per discounted payment. Masked card,
  approval code, basis, discount, bank and store shares, outlet, status
  (reserved, confirmed, cancelled), claim status, the claim it joined, and
  its reclassification entries. The full card number is never stored.
- pos.bank.card.claim: one claim to a bank over confirmed usage of one
  period, with its Excel export, customer invoice, and status (draft,
  sent, paid, rejected, cancelled). A claim that still holds usage lines
  cannot be deleted; cancel or reject it to release the lines first.
- pos.bank.card.claim.column: the Excel columns of a programme claim
  (sequence, value, header).
- pos.bank.card.outstanding: read-only report model, confirmed bank share
  not yet paid, per bank and age bucket.
- pos.bank.card.claim.wizard: the transient wizard that previews usage per
  outlet and creates a claim for a bank and period.

Extensions: pos.payment.method gains "Requires Bank Card Info" and a
default programme; pos.payment carries the card BIN, tier, programme, and
discount shares; pos.order carries the card context (programme, masked
card, reservation status, usage row); res.users carries the hashed Store
Manager PIN.

## Demo data

Installing with demo data creates Bank A and Bank B, Outlet 1 and Outlet 2
(point of sale configurations with a Card Bank payment method that requires
bank card info), and two programmes: Bank A Card Programme (15 percent,
minimum 500,000, maximum discount 100,000, bank share 100 percent, quota of
3 transactions per day, one use per card per day; BIN 900100-900199
Platinum eligible, BIN 900200-900299 Gold excluded) and Bank B Card
Programme (10 percent, maximum discount 75,000, bank share 50 percent,
quota of 1,000,000 in discount amount, one use per card per day; BIN
900300-900399 Signature eligible), and Bank C Card Programme (fixed amount
1,000 per transaction, bank share 100 percent, quota of 50 transactions per
month, one use per card per day; BIN 900400-900499 Classic eligible). Four
confirmed usage rows from the last three days, two per bank and outlet,
populate the claim wizard and the reports on an empty database. Demo card
prefixes 900150, 900350, and 900450 with any last four digits exercise the
cashier dialog.

## Assumptions

- Programme percentages, fixed amounts, minimum, maximum, BIN ranges, and
  names are data, not code; change them on the programme form without a
  deployment. Amounts follow the company currency and its rounding; no
  currency is named in code.
- A shared quota counts every card of the programme together (for example
  50 transactions per month in total). The only per-card counter is the
  daily per-card limit; there is no per-card monthly counter.
- The claim period is one month by default; the wizard accepts any period.
- POS prices are tax-included for the journal entry math: the discount line
  and the bank share already contain tax.
- Offline discounts are refused by default; a programme must explicitly
  allow them and set its per-outlet daily allowance.
- Language is a per-user Odoo setting. Cashiers sharing one account share
  one language, at the register and in the back office.
- Programme names and product names are not translated (one name
  everywhere). The refusal reason of a BIN range is a translatable field.
- Back office status badges keep the default Odoo contrast, and product
  names on order lines added before a language switch keep their language.

## Performance

Detection at the register is local: entering card digits makes no server
call. Measured server side with 500 BIN ranges on one programme (queries
and seconds per call, Odoo 19 test database): loading the ranges when a
session opens 5 queries, 0.006 s; loading the programmes 8 queries,
0.004 s; one detect_card call 6 queries, 0.005 s; one reserve_quota call,
which re-checks eligibility, locks the counters, and writes the usage row,
19 queries, 0.009 s. Applying a discount costs exactly that one
reserve_quota call.

## Limitations

- No EDC terminal integration and no card processing: the cashier types the
  card prefix, the last four digits, and the approval code from the
  terminal slip.
- Marker mode (bank cashback) is stored on the programme but has no
  separate cashier behaviour in this release; a marker programme currently
  behaves like an immediate discount programme. Whether marker mode is used
  at all is a client decision that is still open, so leave the mode on
  Immediate Discount.
- One bank card discount per card payment line, and the per-card daily
  limit counts reservations, so a reservation released at another outlet
  frees the card for the rest of the day.
- There is no POS-side check that the approval code belongs to the card;
  the code is recorded as typed and checked later against terminal reports.

## Acceptance scenarios

Each scenario is an automated test in this module:

1. Platinum card of Bank A on a 1,000,000 purchase: 100,000 discount
   (15 percent capped) and a usage row.
   TestDetection.test_eligible_acceptance_scenario,
   TestPosBankCardFlow.test_paid_order_confirms_usage, and the cashier
   tour test_bank_card_cashier_flow.
2. Tier not included: refused with the tier reason, no usage row.
   TestDetection.test_tier_excluded,
   TestQuota.test_reserve_rejects_excluded_tier, and the cashier tour.
3. Daily quota exhausted: refused with the quota reason, and the sale can
   continue. TestDetection.test_quota_exhausted,
   TestQuota.test_second_reservation_gets_quota_exhausted, and the cashier
   tour continue-without-discount steps.
4. Same card twice in one day: the second use is refused.
   TestDetection.test_card_used_today and
   TestQuota.test_card_daily_limit_on_reserve.
5. Bank B 50:50 programme: half the discount becomes a bank receivable,
   half stays promotion cost. TestDetection.test_discount_half_share and
   TestPosBankCardFlow.test_closing_reclassifies_only_the_bank_share.
6. Refund of a discounted sale: the claim line is cancelled and never
   billed.
   TestPosBankCardFlow.test_refund_cancels_usage_and_keeps_quota_by_default,
   and the TestClaim scenario 7 test asserting cancelled usage stays out
   of the claim.
7. Claim wizard, one bank and one month: the Excel and invoice totals
   equal the usage rows.
   TestClaim.test_scenario_7_wizard_excel_and_invoice_totals_match_usage.
8. Usage graph after two transactions: discount and bank share grow
   accordingly.
   TestReports.test_scenario_8_graph_totals_grow_with_two_transactions.
9. Language switched from English to Indonesian with an order open: the
   order, discount line, and card context stay, and module text turns
   Indonesian. TestPosLanguage.test_set_writes_user_language_and_session_context
   and the language tour test_language_switch_keeps_the_open_order.
10. Single-language database: the Language menu item does not appear. The
    cashier tour test_bank_card_cashier_flow checks the menu on a database
    with one language installed.

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
