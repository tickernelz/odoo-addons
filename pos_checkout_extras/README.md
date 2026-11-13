# POS Checkout Extras

Three checkout additions for the Odoo 19 point of sale, each set per point of sale (counter).

## Customer required

`Customer Required` on a point of sale:

| Mode | Effect |
|---|---|
| Not required | No change. |
| Required at order | Pay opens a dialog until a customer is set. |
| Required at payment | Validate opens a dialog until a customer is set. Fast payment buttons ask too. |

The dialog offers **Select customer** and **Cancel**; picking a customer continues the flow.
The Customer button shows a **Required** badge while the order has none.
Refunds and draft orders are never blocked.

The server checks too: a paid order without a customer, dated after the moment the mode left
`Not required` (`Customer Required Since`), is refused when the till sends it. Orders created
offline before that moment, refunds and drafts are accepted.

## Payment reference

`Payment Reference` on a point of sale adds an **Add Payment Reference** button to the payment
screen. One optional reference per order, up to 64 characters; longer values sent over RPC are
cut to 64. It is printed on the receipt after the payment lines and stored on `pos.order` and,
through a stored related field, on every `pos.payment` of the order. Editing the reference of an
order in the backend updates its payments. The reference is searchable and groupable on Orders
and Payments (pivot and graph views added) and is a field of the POS analysis
(`report.pos.order`). It does not reach accounting (`account.payment`, journal items).

## Analytic accounts per counter

Each point of sale picks its **Analytic Accounts** (`analytic_account_ids`, many to many: one
counter can use several accounts and one account several counters; the account form lists them in
`pos_config_ids`). The accounts make the counter's analytic distribution:

- accounts in different plans each take 100%;
- accounts in the same plan share 100%, evenly by default; **Split** adjusts the percentages.

Adding or removing an account of a plan splits that plan evenly again; the split of other plans is
kept. Upgrading from 19.0.1.0.0 picks the accounts of each existing distribution and leaves the
distribution and the journal items already written untouched.

Each counter also has an **Apply to** choice:

- **Profit and loss lines** (default): journal items on income and expense accounts.
- **All lines**: every journal item of the session.

The distribution is written when a session closes, on:

- the session entry: sales, taxes, cost of goods sold and stock valuation;
- bank closing differences, combined and split payment methods;
- the cash closing difference and cash in/out statement lines;
- the bank payments of the session (All lines only, they hold no profit or loss);
- the product lines of invoices of the session's orders.

A distribution must cover every mandatory plan of the `general` business domain at 100%.
Changing the accounts or the split is logged and only affects sessions closed afterwards.

### Synchronise

Settings, Checkout Extras: **Synchronise this counter** or **Synchronise all counters** opens a
wizard with a period and a mode (only items without a distribution, or replace every
distribution). The preview counts sessions, items, items to update and distributions that would
be replaced. Each click updates up to 10,000 journal items in chunks of 1,000; items refused by
accounting are skipped and counted. Every run is written to **Configuration, Analytic Sync Log**.

### Coverage report

**Reporting, POS Analytic Coverage** prints, per counter and period: sessions, journal items in
scope, items carrying the counter's distribution, items without one and items with another one.

## Settings

Point of Sale, Configuration, Settings, block **Checkout Extras**, or the point of sale form.
The analytic part needs the Analytic Accounting group.

## Notes

- Odoo does not enforce mandatory analytic plans on POS session entries; the coverage report is
  the place to see items without a distribution.
- Uninstalling keeps the distributions already written on journal items.
- Indonesian translation: `i18n/id.po`.

## License

LGPL-3.
