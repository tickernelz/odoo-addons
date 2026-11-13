# POS QRIS Xendit

Dynamic QRIS through Xendit at the Odoo 19 point of sale, and a statement memo per customer payment
that bank reconciliation can match against Xendit statement lines.

## Taking a QRIS payment

1. The cashier picks the **QRIS Xendit** payment method.
2. The till asks the server for a QRIS. The server calls Xendit Payments API v3
   (`POST /v3/payment_requests`, `api-version: 2024-11-11`, channel `QRIS`) and shows the QR code,
   the amount, the reference and the validity in a dialog. Asking again for the same payment line and
   amount reuses the QR while it is valid.
3. The customer scans it with a bank or e-wallet app. Xendit calls the webhook (`payment.capture`),
   the request turns **Paid**, and the till hears it on the point of sale bus and completes the line.
   **Check status** asks Xendit directly when the webhook is late; **Cancel payment** cancels the QR at
   Xendit (a QR already paid stays paid).

Each attempt is a `pos.xendit.qris.request` (Point of Sale > Orders > QRIS Xendit, managers):
reference, Xendit payment request and payment ids, amount, state, and an **Attention** note when
Xendit reported something the till could not settle (a payment after the QR was cancelled or
expired, another amount, a second payment).

The server refuses a QRIS Xendit order payment unless a paid request for that payment line and
amount exists, so a line cannot be marked paid without Xendit.

## Configuration

Payment method, **Integration: Terminal**, **Integrate with: Xendit QRIS**:

| Field | Use |
|---|---|
| Xendit Secret Key | Secret API key (development or production key). Settings administrators only. |
| Xendit Webhook Verification Token | The token from the Xendit dashboard (Webhooks). Settings administrators only. |
| QR Validity (minutes) | 1-2880, default 15. |
| Xendit Webhook URL | Read only. Set it in the Xendit dashboard for payment events. It must be reachable over HTTPS. |

Neither secret is loaded into the till nor readable by point of sale users or managers.

The webhook (`/pos_xendit_qris/webhook`) compares the `x-callback-token` header with the token of the
method that owns the reference (`hmac.compare_digest`); no token or a wrong token is refused (403)
before the body is read. It then checks the payment request id, amount and currency, and is
idempotent: Xendit may retry or send events out of order.

## Statement memo

**Create Statement Memo** (any point of sale payment method, needs **Identify Customer**) writes the
memo of each customer payment booked at session closing as:

```
<method> <customer> <order date> <order reference> <order time>[ - <Xendit reference>]
```

for example `QRIS Xendit khusnul ari 21 October 2610-1-0122 10:51 - 2610-1-0122-57`. The month follows
the language of the user closing the session; date and time are the order time in the timezone of
the user who opened the session. The format lives in `pos.payment._statement_memo`
(`MEMO_DATE_FORMAT`, `MEMO_TIME_FORMAT`).

**Default Customer** fills the customer of an order that has none when it is paid with a method
that identifies customers, so the cashier is not stopped.

Identify Customer makes one customer payment per order payment instead of one per session.
Measured on 200 payments: closing the session went from 0.5-0.8 s and 376 queries to 4.6-4.7 s
and 3,800 queries.

## Reconciliation

The Xendit reference is the `Reference` column of Xendit transaction reports. Odoo's automatic bank
matching splits a journal item reference on ` - ` and matches a part longer than five characters
that appears in exactly one open journal item, so the reference sits after ` - ` in the memo.
A statement line whose label contains the reference is matched to that payment; a reference written
after a plain space is not.

Xendit reports gross `Amount` and `Fee`/`VAT` separately; the payment matches the gross amount and the
fee needs its own statement line or a reconciliation model.

## Limits

- IDR only, whole rupiah, at most 10,000,000 per QR (Xendit QRIS limits).
- Payments only: refunds go through the Xendit dashboard and another payment method at the till.
- Per-reference matching needs one statement line per payment, not one per settlement batch.
- The webhook needs Odoo reachable from Xendit over HTTPS.
