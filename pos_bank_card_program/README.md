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

Icon: the source is static/description/icon.svg; regenerate the PNG with
inkscape static/description/icon.svg --export-type=png --export-filename=static/description/icon.png -w 256 -h 256
(run from the module directory).
