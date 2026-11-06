# odoo19-mcp

Odoo 19 addon `kaitech_mcp`: one MCP (Model Context Protocol) endpoint per database at `POST /mcp/<database>`.
Same client contract as the Odoo 10 module `kaitech_mcp_server` (tool names, argument schemas, key and audit models);
keys start with `o19mcp_`.

## Install

1. Put `kaitech_mcp` in the addons path.
2. Load it server-wide (MCP clients send no Odoo session), in `odoo.conf`:
   `server_wide_modules = base,web,kaitech_mcp` (keep the modules already listed), then restart Odoo.
3. Install the module in each database that should answer MCP calls.
4. The reverse proxy must forward `/mcp/` and the header that carries the key: `Authorization`, `X-API-Key` or
   `X-Auth-Token`.
5. In the database: MCP Server, Overview, Test endpoint must report HTTP 401 without a key.

Every database not served (absent, filtered by dbfilter, module not installed, module not server-wide) answers the
same HTTP 404.

## Tools

ORM path, as the key's Odoo user (access rights and record rules apply): `list_models`, `get_model_fields`,
`search_records`, `count_records`, `read_records`, `create_record`, `update_records`, `delete_records`.

Explore code, off by default per key (Explore code): `describe_model` (one line per field with type, label, compute
method and depends, related, selection keys, defining modules; parent models, extending modules, public methods, ORM
overrides, key and user access), `search_methods`, `describe_method` (signature, decorators, docstring, computed
fields, every override in MRO order with `module/path:line`), `get_method_source`, `search_code` (grep over the
installed modules' folders), `read_source` (`module/path`, py/xml/js/csv/po/pot, real path must stay inside the
module folder), `list_modules`, `describe_module`, `get_view` (the arch the key user sees after inheritance),
`list_actions` (window, server and report actions with their menus), `search_xmlids`, and `list_models` with
`all: true`. They read code and metadata only, never records, settings values or files outside module folders. The
introspection index lives per registry and is rebuilt when Odoo replaces the registry (module install or upgrade).
The resource `odoo://overview` gives a first map (Odoo version, modules not made by Odoo, the key's models).

Call methods, off by default per key (Call methods) and per model (Call column): `call_method` runs one public
business method as the key user (`@api.model` methods without ids). Private `_methods`, `@api.private` methods and
every method the ORM or the `base` model defines (write, unlink, sudo, web_save, ...) are refused. A method that calls
`sudo()` inside is not stopped by `call_method`; the mail gateway methods (`message_process`, `message_route`,
`message_route_verify`, `message_route_process`) are refused by name. The result lists
the stored fields that changed on the records; `dry_run` runs the call and rolls the transaction back (a method that
commits by itself is refused; email or HTTP calls it makes are not undone). Every call is in the call log with its
arguments and the written fields.

SQL path, off by default at three levels (MCP Settings, Allow SQL per key, tables per key); it bypasses Odoo access
rights, record rules, Python constraints, computed fields and tracking: `sql_list_tables`, `sql_select`,
`sql_query` (one read-only SELECT; EXPLAIN checks its tables and functions, PostgreSQL's own column dependencies
check hidden columns whatever the query renames them to, so `*` in a CTE, subquery or UNION over a table with a hidden
column is refused; quotes are read left to right, and backslashes, dollar quotes, comments, a second statement or
unbalanced parentheses are refused), `sql_insert`, `sql_update`, `sql_delete`
(where required, at most 20 rows, `dry_run`). A key acting as a Settings administrator never gets SQL.

Deleting needs the global switch in MCP Settings plus Delete ticked per model or table; at most 20 records per call.

## Key headers

The key may arrive in `Authorization: Bearer o19mcp_...`, `X-API-Key: o19mcp_...` or `X-Auth-Token: o19mcp_...`;
the last two also accept a `Bearer` prefix. The first header carrying a key decides, so a refused key is never
retried from another header, a blank header carries nothing, and a non-Bearer `Authorization` is not a key. The key
is never read from the query string or the body. Every refusal is the same 401 with
`WWW-Authenticate: Bearer realm="mcp"`, so the answer never says which header was read.

## Client configuration

Claude, Add custom connector (that dialog cannot set `Authorization` while OAuth is chosen):

- URL: the endpoint, `https://<host>/mcp/<db>`
- Request header: `x-api-key` = `o19mcp_...`, ticked Required
- Authentication: any value; the key travels in the header above, never in this one

Claude Desktop (through `mcp-remote`):

```json
{"mcpServers": {"odoo-<db>": {"command": "npx", "args": ["-y", "mcp-remote", "https://<host>/mcp/<db>",
  "--header", "Authorization:${MCP_AUTH}"], "env": {"MCP_AUTH": "Bearer o19mcp_..."}}}}
```

Clients with HTTP headers (Cursor, n8n, Hermes; the key may also go in `X-API-Key` or `X-Auth-Token`):

```json
{"mcpServers": {"odoo-<db>": {"type": "http", "url": "https://<host>/mcp/<db>",
  "headers": {"Authorization": "Bearer o19mcp_..."}}}}
```

The key wizard shows every block, filled in, once.

## CI

`lint`, `odoo19-image` (module tests on the official image), `odoo19-pinned-core` (module tests on Odoo core
e07db5ea, a production build), `i18n` (template equals a fresh export, `id.po` fully translated, no
Indonesian outside `i18n/`), `multi-db` (one Odoo process, three databases, real HTTP, official MCP SDK client).
