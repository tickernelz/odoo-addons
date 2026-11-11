# POS Promotions (kai_pos_promotion)

Tiered promotions, exclusive groups and happy hours for the Odoo 19 point of sale. Everything that
Discount & Loyalty already does stays native; this module adds what it cannot do and lets native
programmes take part in the same rules.

## What it adds

- **Tiered promotions** (`kai.pos.promo`, `kai.pos.promo.tier`): spending thresholds with a percentage or a
  fixed amount per order. The highest tier reached applies; thresholds are inclusive. An optional maximum
  discount caps every tier. The discount is rounded half up once per promotion and order.
- **Exclusive groups and priority**, on POS promotions and on Discount & Loyalty programmes
  (`loyalty.program.kai_exclusive_group`, `kai_priority`): within one group only the largest discount of the
  order applies; on equal amounts the lower priority number wins, then the older record. Group names ignore
  case. Programmes without a group behave as before.
- **Happy hours** (`kai.pos.promo.schedule`), on POS promotions and on Discount & Loyalty programmes: days and
  windows in the timezone of the point of sale (`pos.config.kai_timezone`), start inclusive, end exclusive, at
  most four windows that do not overlap. When a window ends before Payment is pressed, the order is
  recalculated and the cashier is told.
- **Cashier screen**: one upsell banner (shortest way to a better discount, or the reached tier), a happy hour
  badge with the time left on product cards, the same banner on the customer display, and the promotion name
  on the order line and receipt.
- **Approval**: Draft, Waiting Approval, Approved, Active, Ended, Stopped. Only drafts can be edited or deleted;
  a promotion already used on an order can only be archived, and dragging the list reorders drafts only.
  The Approver group (POS Promotions privilege) approves; an hourly cron starts and ends promotions by date.
- A warning on the form lists every running promotion and programme that shares the exclusive group.

## How the discount is written

The till writes one order line per promotion and tax combination, with the promotion's discount product and
the taxes of the lines it discounts, so journals, VAT, session analytics and the native sales reports stay as
they are. The basis is the tax-included amount of the counted lines after pricelist and item-level rewards;
excluded products and categories, gift card and eWallet lines, tips, bank card discounts and other promotion
lines are not counted.

Promotion lines cannot be selected, edited or deleted by the cashier, so they never trigger a manager PIN.
Refunding products returns their share of the promotion automatically (proportional; the tier is not
recalculated). Promotions run from the data loaded at session start, so they keep working offline.

## Calculation order

1. Pricelist price.
2. Item-level rewards of Discount & Loyalty (happy hours apply here too).
3. Order-level promotions; exclusive groups are settled here, native programmes included.
4. Coupons, loyalty points and gift cards (native).
5. Bank card promotions (`pos_bank_card_program`), whose basis already contains the promotion lines.

## Models

| Model | Purpose |
|---|---|
| `kai.pos.promo` | Promotion: period, points of sale, counted and excluded products, group, priority, discount product, cap, rounding, happy hour, state |
| `kai.pos.promo.tier` | Threshold, percentage or amount |
| `kai.pos.promo.schedule` | Happy hour window of a promotion or of a `loyalty.program` |
| `pos.order.line` | `kai_promo_id`, `kai_promo_shares` (basis per line, for refunds), `kai_promo_refund_of_id` |

## Translations

Source strings are English. The Indonesian interface lives in `i18n/id.po`; update it after changing a string
with `ci/export-pot.sh` and `python ci/i18n.py check`.

## Limits of this version

- Bundles, purchase-with-purchase, the simulator, Excel import and the two reports are planned for the next
  phases and are not part of this module yet.
- The till uses the clock of the device for happy hours.
- A happy hour that ends after a bank card discount was applied on the payment screen keeps that bank card
  discount; remove and apply it again if the order total changes.
