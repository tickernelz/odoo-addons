# Changelog

## 1.5.2 (Kaitech) - 2026-11-16

- A personal mailbox is its owner's alone, and a team mailbox opens only for its owner and assigned members.
  Administrators no longer read every mailbox: they configure team mailboxes and archive or delete any mailbox,
  but see no folder, conversation, message, attachment or record link of a mailbox they do not own or belong to.
- Members left on a mailbox switched back to personal lose access.

## 1.5.0 (Kaitech) - 2026-11-16

- Forked as kaitech_email_client: UNIR2 plan gate, UNIR2 connection and Sinap AI removed; every feature works without a plan.

## 1.4.1 - 2026-11-15

- **Try it in development (Odoo.sh)**: next to «New version available», a button opens UNIR2, which prepares a development branch with the new version in your Odoo.sh repository (no git). Test it there, then pass it to production from UNIR2 or Odoo.sh.

## 1.4.0 - 2026-11-14

- **Installs on its own from the Odoo Apps Store.** It no longer needs Unir2 Odoo Connector, so installing it next to
  another new plain module no longer stops with "The path unir2_odoo_connector already exists".
- **Connect with UNIR2 built in**: while this Odoo is not connected, a strip at the top of the module asks to connect it
  (administrators get the button, other users are told to ask one); the button is also in the settings of the module.
  The connection is shared: connecting once, from any new plain module or from Unir2 Odoo Connector, connects them all.
  When Unir2 Odoo Connector is installed, it is used for the connection, its dashboard and the update notices.
