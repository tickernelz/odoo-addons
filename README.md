# odoo-addons, Odoo 10.0

Odoo addons, one branch per Odoo version: this is `10.0`; `19.0` holds the addons for Odoo 19.
Each addon sits at the root of the branch, so the checkout itself is an addons path.

| Addon | What it does |
|---|---|
| [`kaitech_mcp_server`](kaitech_mcp_server/README.md) | One MCP (Model Context Protocol) endpoint per database at `POST /mcp/<database>`, same client contract as `kaitech_mcp` on `19.0`. |

## Use

1. Clone the branch: `git clone -b 10.0 https://github.com/tickernelz/odoo-addons.git`.
2. Add the checkout to `addons_path`, then install the addon. Each addon's own README covers its settings.

## Branches

- `10.0`, `19.0`: one per Odoo version, the default is `19.0`.
- Work branches: `10.0-<name>`, merged into `10.0`.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `10.0` and `10.0-*` and on pull requests into `10.0`: flake8 on Python 2.7.18, module tests on the official `odoo:10.0` image (empty database and with business apps), on a 2017 Odoo 10 commit and on a pinned Odoo core commit with PostgreSQL 15, i18n checks, and a multi-database harness.

## License

LGPL-3.0 (`LICENSE`), as declared in each addon manifest.
