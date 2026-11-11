# POS Manager Validation

Sensitive point of sale actions wait for a manager validation PIN, set per point of sale.

## What it does

- Each point of sale picks its validating managers (employees) and ticks the actions that need them, in
  Point of Sale > Configuration > Settings > Manager Validation. The section appears when Log in with Employees
  is on. Every action is off by default: a point of sale with nothing ticked behaves exactly as before.
- Twelve actions can be guarded: closing the session (Close Register and the backend button), deleting an order,
  removing an order line, validating a payment, applying a discount (line or global), changing a price,
  decreasing a quantity, cash in/out, creating a product, changing the fiscal position, changing the pricelist,
  and refunding.
- The manager types a separate POS validation PIN of six to twelve digits, set in the HR Settings tab of the
  employee. It is not the PIN used to log in to the point of sale and must differ from it. The PIN can be typed
  on the dialog keypad (mouse or touch) or on a physical keyboard; scanned barcodes are ignored while the
  dialog is open.

## Security

- The validation PIN is stored as a PBKDF2-SHA512 hash and checked on the server only
  (`pos.session.verify_manager_pin`). It is never sent to the point of sale and never written to a log.
  The login PIN of pos_hr is unchanged and stays a browser-side check, so it is never used for validation.
- Five wrong PINs within fifteen minutes lock validation for that manager everywhere and for that session
  (or, in the backend, for that user and point of sale) until the oldest of those five is fifteen minutes old.
  A right PIN resets the count. A POS manager can unlock from Point of Sale > Reporting > Manager Validations.
- Every request is logged there: point of sale, session, manager, cashier, action, result, and the order it
  was for. The PIN is not stored.
- A validating manager who is the logged-in cashier types the validation PIN once; after that the
  point of sale does not ask again until another employee takes the register.
- A point of sale without a connection refuses guarded actions until the server is reachable again.

## Server checks

Actions that reach the server without the point of sale screen are checked there too:

| Path | Check |
|---|---|
| Closing a session (POS, backend button, and every RPC route: closing control, validate, close) | Refused without a validation for that session in the last ten minutes |
| Cash in/out (RPC) | Refused without a validation; one validation per move |
| Refund and payment from the backend order form | Opens the validation wizard first; refused without a validation |
| Cancelling or deleting a draft order with lines (any route that writes the cancel state or unlinks it) | Refused without a validation for that order, except while closing a validated session, on restaurant points of sale, and for the superuser |
| Synced orders with a line discount or a global discount, another pricelist or fiscal position, a payment or a refund | Saved, and marked "Unvalidated Actions" when no validation exists for that order |

Removing a line, decreasing a quantity, changing a price and creating a product are guarded on the point of sale
screen only. An order that reaches the server carries no trace of the first two and of product creation, and a
manual price cannot be told apart from prices other modules set: Odoo marks loyalty rewards and lines added
with a price as manual too.

## Working with other point of sale modules

- Depends on `pos_hr` only, and no module of this repository depends on it, so the dependency graph stays
  one-way (`ci/manifest_graph.py` fails the build on a cycle).
- Patches OWL methods with `super` calls only and adds no method another module defines. With
  `pos_checkout_extras`, Validate asks for the required customer first and the manager PIN second, also when
  `pos_loyalty` checks the order before the core validation does.
- Lines that other modules add or remove programmatically (loyalty rewards, promotions) never ask for a PIN:
  only the cashier's own discount, price, quantity and line removal do.

## Limits

- The guard on line removal, quantity decrease and product creation is only as strong as the point of sale client.
- Restaurant points of sale move and merge orders client-side, so cancelling their orders is not checked on the server.
- The server knows one cashier per session: when two devices share a session, the cashier set last decides
  whether the manager-at-the-register shortcut applies to calls made outside the point of sale screen. The screen
  itself still asks for the PIN.
- A self-order kiosk cannot guard order deletion: its customers cancel their own orders, so the setting is refused
  on a point of sale in kiosk mode.
- A self-order kiosk with session closing validated is closed from the backend session form, which asks for the PIN.
- With "Restrict Price Modifications to Managers" also on, a cashier without price rights is still refused after the PIN.

## Tests

```
odoo-bin -d <db> -i pos_hr_manager_validation --test-enable --test-tags /pos_hr_manager_validation --stop-after-init
```
