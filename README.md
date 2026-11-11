# odoo-addons, Odoo 19.0

Odoo addons, one branch per Odoo version: this is `19.0`; `10.0` holds the addons for Odoo 10.
Each addon sits at the root of the branch, so the checkout itself is an addons path.

| Addon | What it does |
|---|---|
| [`kai_pos_promotion`](kai_pos_promotion/README.md) | Point of sale promotions on top of Discount & Loyalty: tiered promotions, exclusive groups with priority, happy hours per point of sale timezone, an upsell banner and promotion approval. |
| [`kaitech_mcp`](kaitech_mcp/README.md) | One MCP (Model Context Protocol) endpoint per database at `POST /mcp/<database>`: per-key access to records, read-only SQL, code exploration and method calls, with a call log. |
| [`pos_hr_manager_validation`](pos_hr_manager_validation/README.md) | Sensitive point of sale actions wait for a manager validation PIN checked on the server, set per point of sale. |

## Use

1. Clone the branch: `git clone -b 19.0 https://github.com/tickernelz/odoo-addons.git`.
2. Add the checkout to `addons_path`, then install the addon. Each addon's own README covers its settings.

## Branches

- `19.0`, `10.0`: one per Odoo version, the default is `19.0`.
- Work branches: `19.0-<name>`, merged into `19.0`.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `19.0` and `19.0-*` and on pull requests into `19.0`: lint, module tests on the official `odoo:19.0` image and on a pinned Odoo core commit, i18n checks, and a multi-database harness.

## License

LGPL-3.0 (`LICENSE`), as declared in each addon manifest.
