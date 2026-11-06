# MCP contract (Odoo 10 and Odoo 19)

One client configuration works against both module versions. Anything not listed here may differ between versions only where the Odoo version forces it.

## Endpoint

- `POST https://<host>/mcp/<database>`, one JSON-RPC 2.0 message per body, answer `application/json` (no SSE, no `Mcp-Session-Id`).
- `Authorization: Bearer o10mcp_<43 base64url chars>` (Odoo 10) / `o19mcp_...` (Odoo 19). Stored as sha256 plus the first 8 secret characters as `key_index`.
- The same key is also read from `X-API-Key: <key>` and `X-Auth-Token: <key>` (clients that cannot send `Authorization`, such as Claude's custom connector request headers). Order: `Authorization` (Bearer scheme with a value), then `X-API-Key`, then `X-Auth-Token`; the first that holds a value is the only one used, so a refused key is never replaced by the next header's value and a valid `Authorization` wins over any other header. In the two `X-` headers the value is the bare key or `Bearer <key>`. A key in the query string or the body is never read. Every refusal is the same 401 whichever header carried the key.
- Protocol versions: `2026-07-28` (stateless, `server/discover`, `resultType`, `_meta` envelope, `Mcp-Protocol-Version`/`Mcp-Method`/`Mcp-Name` headers checked) and `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05` (`initialize`).
- HTTP: 200 answer, 202 notification, 400 malformed / header mismatch / unsupported version (modern era), 401 key missing, wrong, revoked, expired, superuser, archived user (`WWW-Authenticate: Bearer`), 403 foreign `Origin`, 404 database not served (one identical body for every cause), 405 non-POST (answered before the database is looked up, so it reveals nothing), 413 body over 1 MB, 429 rate limit with `Retry-After`.
- JSON-RPC codes: -32700, -32600, -32601, -32602, -32603, -32001 refused here, -32020 header mismatch, -32022 unsupported protocol version, -32029 rate limit.
- Odoo and SQL business errors are tool results with `isError: true` and `structuredContent: {"error": "<message>"}`; never a traceback.

## Tools (fixed order in `tools/list`; a tool is listed only when the key could use it)

ORM path (runs as the key's Odoo user, ACL and record rules apply):

| Tool | Arguments | structuredContent |
|---|---|---|
| `list_models` | `query?`, `limit?` 1..100 = 50 | `{models: [{model, name, can: {read, create, update, delete}}], returned, has_more}` |
| `get_model_fields` | `model`, `fields?` | `{model, fields: {name: {type, string, required, readonly, relation?, selection?, help?, relation_readable?}}}` |
| `search_records` | `model`, `domain?`, `fields?`, `limit?` = 20, `offset?`, `order?`, `include_archived?` | `{records, returned, has_more, next_offset}` |
| `count_records` | `model`, `domain?`, `include_archived?` | `{count}` |
| `read_records` | `model`, `ids`, `fields?` | `{records, missing_ids, not_returned_ids?}` |
| `create_record` | `model`, `values` | `{id, display_name}` |
| `update_records` | `model`, `ids`, `values` | `{updated}` |
| `delete_records` | `model`, `ids` (at most 20) | `{deleted: [{id, display_name}]}` |

`call_method` (listed only with "Call methods"): a method that calls `sudo()` inside is not stopped by `call_method`; the mail gateway methods `message_process`, `message_route`, `message_route_verify`, `message_route_process` are refused by name.

SQL path (runs as the database role: bypasses ACL, record rules, Python constraints, computed fields, mail tracking; off by default at three levels):

| Tool | Arguments | structuredContent |
|---|---|---|
| `sql_list_tables` | `query?` | `{tables: [{table, can: {select, insert, update, delete}, primary_key, columns: [{name, type, nullable}]}]}` |
| `sql_select` | `table`, `columns?`, `where?`, `order?`, `limit?` = 20, `offset?` | `{rows, returned, has_more, next_offset}` |
| `sql_query` | `query` (one SELECT or WITH ... SELECT), `limit?` = 20 | `{columns, rows, returned, has_more}` |
| `sql_insert` | `table`, `rows` (1..20 objects) | `{inserted, keys}` |
| `sql_update` | `table`, `values`, `where` (required, non-empty), `dry_run?` = false | `{updated, keys, dry_run}` |
| `sql_delete` | `table`, `where` (required, non-empty), `dry_run?` = false | `{deleted, keys, dry_run}` |

Shared argument rules:
- `domain` and `where`: Polish-notation list of `[name, operator, value]` terms and `'&'`, `'|'`, `'!'`; implicit AND between terms. Operators: `= != < <= > >= in, not in, like, ilike, not like, not ilike, =like, =ilike` (`child_of` on the ORM path only). Values are JSON scalars or lists of scalars; SQL values are always bound parameters. `= null` and `!= null` become `IS NULL` / `IS NOT NULL`.
- `order`: `"name asc, id desc"`, only readable columns or stored fields.
- SQL identifiers are checked against `information_schema` of the `public` schema and always quoted.
- `sql_update`/`sql_delete` change at most 20 rows (`max_delete` for delete); the matches are counted first, and more matches change nothing and return `isError`. `dry_run` runs the statement in a savepoint, rolls back, and reports the count and keys.
- No `sql_*` tool sends anything to Postgres unless all three switches allow it for that tool's action (setting, the key's `allow_sql`, a table ticked for the action); otherwise the call answers "There is no tool named ...", the same as an unknown tool.
- `sql_query` accepts one statement only. The text is split once into literals, quoted names and plain text; a quote inside the other kind of quote, an escape-string prefix (`E'`, `B'`, `X'`), a dollar quote, a backslash, a `$`, a comment or a second `;` outside a string refuses the query, and so does an unbalanced parenthesis. The same split decides what the plan checks see, so an alias such as `"'"` cannot hide a function or a `; `. `standard_conforming_strings` is set on for the request.
- `sql_query` runs in a read-only savepoint with `statement_timeout` (setting `sql_statement_timeout_ms`, default 5000). Before running, `EXPLAIN (VERBOSE, FORMAT JSON)` must show only tables the key may select, no whole-row reference, and only functions from a fixed allow-list; hidden columns are checked as described below; anything else is refused.
- `sql_insert` fills `create_uid`, `write_uid` with the key's user and `create_date`, `write_date` with the current UTC time, and `sql_update` fills `write_uid` and `write_date`, when the table has those columns; callers may not set these four columns.
- In `sql_query`, the columns a query reads come from Postgres itself: the query is compiled as a temporary view inside a savepoint and its column dependencies (`pg_depend`) are checked, then the view is rolled back. Any hidden column read anywhere, under any alias, refuses the query; `*` over a table with a hidden column is refused.

## Permissions and settings

- Key (`kaitech.mcp.key`): `name`, `user_id` (never uid 1 / superuser, never portal), `key_index`, `key_hash`, `expires_at` (default 90 days), `revoked_at`, `revoked_by`, `rotated_from_id`, `rate_per_minute` 60, `writes_per_minute` 20, `max_read` 200, `max_write` 100, `max_delete` 20, `allow_sql` (default false), `permission_ids`, `sql_table_ids`.
- ORM permission (`kaitech.mcp.permission`): `key_id`, `model_id`, `can_read`, `can_create`, `can_update`, `can_delete`, `field_ids`.
- SQL table permission (`kaitech.mcp.sql.table`): `key_id`, `table_name`, `can_select`, `can_insert`, `can_update`, `can_delete`.
- Settings (`ir.config_parameter` prefix `kaitech_mcp_server.`): `delete_enabled` false, `sql_enabled` false, `sql_statement_timeout_ms` 5000, `audit_retention_days` 90, `default_expiry_days` 90, `default_rate_per_minute` 60, `default_writes_per_minute` 20, `default_max_read` 200, `default_max_write` 100, `default_max_delete` 20, `extra_forbidden_models`, `extra_forbidden_fields`, `extra_forbidden_tables`.
- Deleting (ORM or SQL) needs `delete_enabled` plus delete ticked per model or per table. SQL needs `sql_enabled`, the key's `allow_sql`, and the table ticked.
- Built-in forbidden models: `ir.*`, `res.users*`, `res.groups*`, `base.*`, `bus.*`, `kaitech.mcp.*`, `res.device*`, `auth_totp.*`. Built-in forbidden tables: `ir_*`, `res_users*`, `res_groups*`, `base_*`, `bus_*`, `kaitech_mcp_*`, `res_company_users_rel`, `rule_group_rel`, `res_device*`, `auth_totp*`, every table outside the `public` schema. Built-in forbidden fields and columns: `password`, `password_crypt`, `new_password`, `signup_token`, `oauth_access_token`, `access_token`, `totp_secret`, binary fields and `bytea` columns; SQL columns matching `*password*`, `*token*`, `*secret*`, `totp*` are hidden too. Settings add to these lists, never remove.
- A key bound to a Settings administrator (`base.group_system`) cannot use SQL: refused when saving `allow_sql`, and the `sql_*` tools disappear at request time if the user gains that group later.
- Version-forced differences: module `kaitech_mcp_server` / setting prefix `kaitech_mcp_server.` (Odoo 10) vs `kaitech_mcp` / `kaitech_mcp.` (Odoo 19, another addon already uses the name `kaitech_mcp_server`); refused superusers uid 1 (Odoo 10) vs uid 1 and 2 (Odoo 19); jsonb translated columns accept `{"en_US": "..."}` objects in `sql_insert`/`sql_update` (Odoo 19 only).
- Groups: `MCP / Manager` (keys, permissions, overview, read audit) and `MCP / Auditor` (read audit); Settings for `base.group_system`, checked in the settings methods themselves. A manager can bind a key only to a user whose groups are all groups the manager holds.

## Audit (`kaitech.mcp.call`), one row per audited request, committed even when the call rolls back

`key_id`, `key_name`, `user_id`, `method`, `tool`, `is_write`, `model` (model or table), `record_ids` (ids or primary keys, first 50), `record_count`, `fields_written` (names only, never values), `deleted_names` (ORM delete), `statement` (SQL text with `%s` placeholders, values never stored), `dry_run`, `result` (`ok`, `refused`, `limited`, `error`, `invalid_arguments`), `reason`, `duration_ms`, `client_name`, `client_version`, `remote_addr`, `protocol_version`, `create_date`.

## Language

Tool names, descriptions, schemas, protocol errors: English for every caller. Record data and Odoo error messages: the language of the key's user. Screens: `i18n/id.po`.
