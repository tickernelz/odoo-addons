# Kaitech Email Client

Unified inbox inside the Odoo 19 backend: Gmail / Google Workspace and Microsoft 365 (OAuth2) and any
IMAP/SMTP account, personal and team mailboxes with read / reply / manage roles, conversations linked to
contacts.

## Origin

Derived from **Email Client for Odoo Full** 19.0.1.4.1 by New Plain (https://www.newplain.com),
published under the GNU Lesser General Public License v3 (see `LICENSE`). Copyright of the original
code stays with New Plain; Kaitech's changes are published under the same license.

## What Kaitech changed

- Technical name `kaitech_email_client`, so it never clashes with the original module. Model names
  (`umc.*`) are kept.
- Removed the UNIR2 plan gate: `umc.license`, `plan_gate.requires_plan`, the UNIR2 connection and its
  6-hourly verification cron, the update notices and the plan strip. Every feature works without a plan
  and the module makes no call to UNIR2.
- Removed the Sinap AI classification (a paid cloud service of UNIR2) and its job queue.
- Removed the vendor support links from the settings and the inbox.

See `CHANGELOG.md` for the features Kaitech added on top.
