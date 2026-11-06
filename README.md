# odoo19-mcp

Odoo 19 addon `kaitech_mcp`: one MCP (Model Context Protocol) endpoint per database at `POST /mcp/<database>`.
Same client contract as the Odoo 10 module `kaitech_mcp_server` (tool names, argument schemas, key and audit models);
keys start with `o19mcp_`.

## Install

1. Put `kaitech_mcp` in the addons path.
2. Load it server-wide (MCP clients send no Odoo session), in `odoo.conf`:
   `server_wide_modules = base,web,kaitech_mcp` (keep the modules already listed), then restart Odoo.
3. Install the module in each database that should answer MCP calls.
4. The reverse proxy must forward `/mcp/` and the `Authorization` header.
5. In the database: MCP Server, Overview, Test endpoint must report HTTP 401 without a key.

Every database not served (absent, filtered by dbfilter, module not installed, module not server-wide) answers the
same HTTP 404.

## Tools

ORM path, as the key's Odoo user (access rights and record rules apply): `list_models`, `get_model_fields`,
`search_records`, `count_records`, `read_records`, `create_record`, `update_records`, `delete_records`.

SQL path, off by default at three levels (MCP Settings, Allow SQL per key, tables per key); it bypasses Odoo access
rights, record rules, Python constraints, computed fields and tracking: `sql_list_tables`, `sql_select`,
`sql_query` (one read-only SELECT checked with EXPLAIN), `sql_insert`, `sql_update`, `sql_delete`
(where required, at most 20 rows, `dry_run`). A key acting as a Settings administrator never gets SQL.

Deleting needs the global switch in MCP Settings plus Delete ticked per model or table; at most 20 records per call.

## Client configuration

Claude Desktop (through `mcp-remote`):

```json
{"mcpServers": {"odoo-<db>": {"command": "npx", "args": ["-y", "mcp-remote", "https://<host>/mcp/<db>",
  "--header", "Authorization:${MCP_AUTH}"], "env": {"MCP_AUTH": "Bearer o19mcp_..."}}}}
```

Clients with HTTP headers (Cursor, n8n, Hermes):

```json
{"mcpServers": {"odoo-<db>": {"type": "http", "url": "https://<host>/mcp/<db>",
  "headers": {"Authorization": "Bearer o19mcp_..."}}}}
```

The key wizard shows both, filled in, once.

## CI

`lint`, `odoo19-image` (module tests on the official image), `odoo19-pinned-core` (module tests on Odoo core
e07db5ea, a production build), `i18n` (template equals a fresh export, `id.po` fully translated, no
Indonesian outside `i18n/`), `multi-db` (one Odoo process, three databases, real HTTP, official MCP SDK client).
