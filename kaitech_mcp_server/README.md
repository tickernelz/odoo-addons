# MCP Server for Odoo 10 (kaitech_mcp_server)

Lets MCP clients (Claude Desktop, Cursor, n8n, Hermes, any client of the Model Context Protocol) read and change data in one Odoo 10 database, as one Odoo user per key, limited to the models, tables and actions an administrator ticks. Every call is in a call log.

The wire contract (endpoint, tools, arguments, errors, audit) is shared with the Odoo 19 module and written down in `CONTRACT.md` at the repository root.

## Install

1. Copy `kaitech_mcp_server` into a directory of `addons_path`. It depends only on `base` and `web` and needs no Python package beyond Odoo 10's own.
2. Load it server-wide. MCP clients send no Odoo session, so on a server with more than one database Odoo would not know which database a request is for and would answer 404. In `odoo.conf`:

       server_wide_modules = base,web,kaitech_mcp_server

   Restart Odoo.
3. Make sure the reverse proxy passes `/mcp/` and the `Authorization`, `X-API-Key` and `X-Auth-Token` headers to Odoo (a plain `location /` that proxies everything is enough).
4. In each database that should answer MCP: Apps, Update Apps List, then install **MCP Server**. A database without the module answers 404.
5. MCP Server (its own app in the main menu), Overview, **Test endpoint**. "HTTP 401 without a key" means routing and database selection work.

Rollback: uninstall the module in each database, remove it from `server_wide_modules`, restart.

## Use

1. Create a dedicated internal Odoo user for the client with only the groups it needs. The Administrator (uid 1) is refused: it bypasses every access right.
2. MCP Server, **Create key**: name the client, pick the user, tick models and actions (Read, Create, Update, Delete, Call methods), and the switches Explore code and Call methods when the client should read the code or run methods. The key is shown once with ready-made client configurations; Odoo keeps only its sha256.
3. Configure the client with the endpoint `https://<host>/mcp/<database>` and the key in one of three headers: `Authorization: Bearer <key>`, `X-API-Key: <key>` or `X-Auth-Token: <key>`. The first of them that holds a value is used, in that order; a refused key is not replaced by the next header, and a key in the query string or the body is never read. In `X-API-Key` and `X-Auth-Token` the value may be the bare key or `Bearer <key>`.

Claude Desktop (through `mcp-remote`):

    {"mcpServers": {"odoo-<database>": {"command": "npx",
      "args": ["-y", "mcp-remote", "https://<host>/mcp/<database>", "--header", "Authorization:${MCP_AUTH}"],
      "env": {"MCP_AUTH": "Bearer <key>"}}}}

Clients that take HTTP headers (Cursor, n8n, Hermes):

    {"mcpServers": {"odoo-<database>": {"type": "http", "url": "https://<host>/mcp/<database>",
      "headers": {"Authorization": "Bearer <key>"}}}}

Claude (Add custom connector, claude.ai or Claude Desktop): URL `https://<host>/mcp/<database>`; under Request headers choose `x-api-key` and paste the key as the value. Claude sends the value exactly as typed and does not allow an `Authorization` header together with OAuth, so `x-api-key` is the header to use there.

Keys can be tested (Test key), rotated (new secret, same permissions, old key revoked) and revoked (effective on the next request). Default expiry is 90 days.

## Tools

Record tools run as the key's user, so Odoo access rights, record rules and multi-company rules apply on top of the key's permissions: `list_models`, `get_model_fields`, `search_records`, `count_records`, `read_records`, `create_record`, `update_records`, `delete_records`.

SQL tools run as the database role and bypass Odoo access rights, record rules, constraints, computed fields and tracking: `sql_list_tables`, `sql_select`, `sql_query` (one read-only SELECT, checked against the query plan), `sql_insert`, `sql_update`, `sql_delete`. They are off by default at three levels: MCP Settings, Allow SQL on the key, and the key's table list. Update and delete need a where condition, change at most 20 rows, and support `dry_run`. Keys bound to Settings administrators cannot use SQL.

Code tools (key switch **Explore code**) are read-only and return structure and source, never records: `list_models` with `all: true` (and `field` to find the models that have a field), `describe_model`, `search_methods`, `describe_method`, `get_method_source`, `search_code` (literal or regex), `read_source`, `list_modules`, `describe_module`, `get_view`, `list_actions`, `search_xmlids`. A client that has just connected learns the order from the server instructions (list_modules, describe_model, list_models with all and field, search_methods or describe_method, get_method_source, search_code and read_source, get_view and list_actions) and can read the resource `odoo://overview`. A regex search is POSIX extended (`grep -E`, no backreferences) and runs as a child process that is killed after 15 seconds, so a pattern that backtracks badly cannot hang the worker; a literal search stays in Python. `read_source` opens a file without following a symlink. Source reads stay inside the installed modules and the file types py, xml, js, csv and po; nothing outside the addons folders and nothing from `ir.config_parameter` is ever returned. Introspection is cached per registry and rebuilt after a module install or upgrade.

`call_method` (key switch **Call methods** and a Call methods tick per model) runs a public business method such as `action_confirm` as the key's user, so access rights and record rules apply; `context` adds context keys. Methods that start with `_` and the ORM methods (`write`, `unlink`, `create`, `sudo` and the rest) are refused, and so are the mail gateway methods that take the model or the user they run as from their arguments (`message_process`, `message_route`, `message_route_verify`, `message_route_process`, `message_get_default_recipients`, `message_get_reply_to`, `message_partner_info_from_emails`). A business method that calls `sudo()` itself runs that part with more rights than the key's user; `context` may not carry `tracking_disable`, `mail_notrack`, `mail_create_nolog` or `force_company`, and a standard `ir.sequence` number a dry run draws stays used (a `no_gap` one is rolled back). With `dry_run` the method runs in a savepoint and the database is rolled back; the answer lists the result and the stored fields that changed. A method that commits or rolls back itself is refused in a dry run, and effects outside the database (a mail sent at once, a file, an outside call) are not undone.

Deleting (records or rows) is off by default: MCP Settings, Allow deleting, then Delete per model or table; at most 20 records per call; deleted ids and names are kept in the call log.

Never exposed: technical models and tables (`ir.*`, `base.*`, `bus.*`), users, groups, devices and TOTP data, the MCP models themselves, password and token fields, binary fields, SQL columns that look like secrets. MCP Settings can add models, fields and tables to these lists.

## Security notes

- One endpoint per database (`/mcp/<database>`); keys, permissions, limits and the call log live in that database. A database that does not exist, is rejected by `dbfilter`/`db_name` for the request host, or lacks the module, answers the same 404.
- Only Settings administrators can read or change MCP Settings; only MCP Managers create keys, and only for users whose groups they hold themselves.
- Rate limits per key (default 60 calls and 20 writes per minute) are counted from the call log under a per-key advisory lock, so they hold across prefork workers.
- Requests from another site's pages (`Origin` of another host) are refused; bodies over 1 MB are refused.
- The call log stores tool, model or table, record ids or primary keys, field names written and SQL text with placeholders; never field values.

## Language

Screens, messages and labels are English in the sources; Indonesian comes from `i18n/id.po` for users whose language is Indonesian (`id_ID`). Tool names, descriptions and protocol errors are English for every client; record data and Odoo error messages follow the language of the key's user. After changing `id.po`, upgrade with `-u kaitech_mcp_server --i18n-overwrite`.

## Assumptions (plan rev 3, questions Q2 to Q7)

- Q2: the production server stays in use long enough to need MCP; the module is generic Odoo 10 either way.
- Q3: the production server serves one database, `dbfilter` empty, 5 prefork workers (checked read-only on 6 Nov 2026); the module works the same with many databases.
- Q4: clients send `Authorization: Bearer` (Claude Desktop through mcp-remote, Cursor, n8n, Hermes) or the key in `X-API-Key` / `X-Auth-Token` (Claude custom connector request headers); OAuth for the claude.ai web connector is not included.
- Q5: deployment through backup, copy of addons, `server_wide_modules`, restart, install, Test endpoint.
- Q6: the production server runs Python 2.7.18 with `hmac.compare_digest`; older 2.7 builds without it use a constant-time fallback.
- Q7: `id_ID` is active on the production server.

## Compatibility

Module tests (96) and harnesses, per target. "CI" jobs run on every pipeline of the repository; the production copy run is local because the data stays off CI.

| Target | Odoo build | Python | PostgreSQL | Database | Result | Where |
|---|---|---|---|---|---|---|
| Official image `odoo:10.0` | 10.0-20190816 (Community) | 2.7.9 | 9.6 | new, empty, no demo data | 96 passed | CI `odoo10-image-empty-db` |
| Same image with business apps | 10.0-20190816 | 2.7.9 | 9.6 | new, with sale, account, stock, purchase | 96 passed | CI `odoo10-image-sale-account-stock` |
| Old 10.0 branch | commit `cb01cfe` (29 Jun 2017) | 2.7.18 | 9.6 | new, empty | 96 passed | CI `odoo10-2017-build-py2718` |
| Pinned production build | commit `28c3f51` (18 May 2021), the production pinned requirements | 2.7.18 | 15 | new, empty | 96 passed | CI `odoo10-pinned-core-py2718-pg15` |
| Copy of production data | commit `28c3f51`, the production `pip freeze` and custom addons | 2.7.18 | 15 | copy of the production database (1.4 GB, every module installed there) | 96 passed | local, 6 Nov 2026 |
| Three databases, one process | `odoo:10.0`, 2 prefork workers | 2.7.9 | 9.6 | `db_a`, `db_b` with the module, `db_c` without | open, dbfilter `^%d$`, `list_db = False`, negative control without server-wide, official MCP SDK 2.3.0 client in both protocol eras: passed | CI `multi-db` |
| Odoo 10 Enterprise | - | - | - | - | not tested: no Enterprise 10 build available | - |
| Python 2.7 older than 2.7.7 (no `hmac.compare_digest`) | - | - | - | - | not run on such an interpreter; the constant-time fallback has its own unit test | - |
| Production server | commit `28c3f51`, Python 2.7.18 | 2.7.18 | 15 | production | 10.0.1.0.0 (records, SQL, audit) live since 6 Nov 2026; the code and method tools go live after merge | - |

Found only on the production copy and fixed: seven custom models override `fields_get(self, fields=None)`, so the module never passes `attributes=` to `fields_get`.

## Performance

Overhead of the MCP layer against the same `search_read` over XML-RPC, 60 calls each, `odoo:10.0` with 2 workers (multi-db harness, scenario `perf`):

| Records | MCP p50 / p95 | XML-RPC p50 / p95 | p95 overhead |
|---|---|---|---|
| 20 | 9.5 / 13.7 ms | 2.6 / 3.5 ms | 10.2 ms (CI runner: 18.8 ms) |
| 200 | 17.4 / 21.7 ms | 9.9 / 11.6 ms | 10.1 ms (CI runner: 19.1 ms) |

Queries per call (`harness/query_count.py`, `odoo:10.0`): `count_records` 13, `search_records` 14 for 20 or 200 records, `create_record` of a partner 97 (Odoo's own create of `res.partner` included). The fixed part (registry signal check, key lookup, rate-limit lock and audit insert, permission and settings read, audit update) is about 11 queries per call and does not grow with the number of records.

### Code and method tools

Measured on the production copy (1.4 GB, 114 installed modules, 1,563 models), Python 2.7.18, PostgreSQL 15, one worker, `localhost`, on a shared box with load 5 to 9. Latency is the median of three passes of 30 warm calls each (15 for `search_code`); cold is the first call after a restart. Result tokens are counted with tiktoken `o200k_base`, a proxy: other models tokenise differently. `search_code_regex` is a regex over one module with no match; `search_code_scan` reads every py, xml, js and csv file with no match; `search_code_scan_all_types` adds po files.

| Tool call | p50 s | p95 s | cold s | result tokens |
|---|---|---|---|---|
| list_models | 0.012 | 0.019 | 0.027 | 225 |
| get_model_fields | 0.014 | 0.020 | 0.051 | 2394 |
| search_records | 0.107 | 0.133 | 0.150 | 35354 |
| list_models_all | 0.015 | 0.022 | 0.026 | 1940 |
| list_models_all_field | 0.019 | 0.023 | 0.031 | 2900 |
| describe_model | 0.011 | 0.014 | 0.027 | 2412 |
| search_methods_model | 0.009 | 0.011 | 0.020 | 46 |
| search_methods_all | 0.014 | 0.019 | 0.081 | 1393 |
| describe_method | 0.010 | 0.012 | 0.033 | 128 |
| get_method_source | 0.009 | 0.012 | 0.032 | 260 |
| search_code_module | 0.011 | 0.014 | 0.031 | 875 |
| search_code_regex | 0.010 | 0.012 | 0.029 | 30 |
| search_code_all | 0.041 | 0.049 | 0.096 | 2058 |
| search_code_scan | 0.391 | 0.438 | 0.893 | 31 |
| search_code_scan_all_types | 1.447 | 1.613 | 2.074 | 31 |
| read_source | 0.010 | 0.011 | 0.031 | 3326 |
| list_modules | 0.018 | 0.021 | 0.029 | 2775 |
| describe_module | 0.022 | 0.026 | 0.036 | 1102 |
| get_view | 0.037 | 0.046 | 0.104 | 4924 |
| list_actions | 0.020 | 0.027 | 0.994 | 1032 |
| search_xmlids | 0.013 | 0.018 | 0.029 | 180 |
| call_method_dry_run | 0.116 | 0.161 | 0.225 | 131 |

No existing tool got slower: with the same data and the same key, interleaved calls of the old and the new module (200 rounds each) gave, for a key without the new switches, `list_models` p50 11.6 and 11.9 ms, `get_model_fields` 12.9 and 12.9 ms, `search_records` (20 records) 142.6 and 143.0 ms, `count_records` 17.4 and 17.1 ms, with identical response bytes. For a key with the switches the numbers are as close (`list_models` 10.4 and 10.5 ms); its `list_models` answer grows from 1,549 to 1,705 bytes because each model also shows whether methods may be called.

`tools/list` and the server instructions cost tokens only for keys that switch the new tools on: a key with Explore code and Call methods lists 17 tools in 3,558 tokens (13,401 bytes) against 1,004 (3,741 bytes) for the same key without the switches, and its instructions grow from 69 to 219 tokens. Keys without the switches see exactly what they saw before.

The first `list_actions` call of a process takes about 1.0 s (it computes the menus the key's user may see once). A `search_code` that matches nothing reads every py, xml, js and csv file of the 114 modules: 0.39 s warm and 0.9 s cold; with po files too, 1.4 s warm and 2.1 s cold. Every other call has a p95 under 0.17 s warm.
