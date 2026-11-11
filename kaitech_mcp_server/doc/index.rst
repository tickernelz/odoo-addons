MCP Server for Odoo — Technical Guide
======================================

.. contents:: Table of Contents
   :depth: 3
   :local:

Overview
--------

MCP Server for Odoo exposes your Odoo 19 instance as a
`Model Context Protocol (MCP) <https://modelcontextprotocol.io>`_ server.
AI clients — Claude, ChatGPT, Gemini, Cursor, n8n, LangChain, crewAI — connect
to live Odoo data through a single HTTP endpoint with OAuth 2.0 or Bearer token
authentication.

- **Protocol:** MCP 2025-03-26 (Streamable HTTP) + legacy SSE
- **Auth:** OAuth 2.0 Authorization Code + PKCE S256, PAT Bearer tokens
- **Tools:** 34+ MCP tools (CRUD, analytics, BI, portal pages, async jobs, module generator)
- **Odoo version:** 19.0 (Community and Enterprise)
- **License:** OPL-1

Requirements
------------

System Requirements
~~~~~~~~~~~~~~~~~~~

- Odoo 19.0 (Community or Enterprise)
- Python 3.11+
- PostgreSQL 15+
- Odoo must be reachable over HTTPS in production (required for OAuth 2.0 redirects)

Optional Python Packages
~~~~~~~~~~~~~~~~~~~~~~~~

These are installed separately if you want the optional features:

.. code-block:: bash

   # For distributed Redis rate limiting
   pip install redis

   # For XLSX export (odoo_export_xlsx tool)
   pip install xlsxwriter

Both packages fall back gracefully if not installed:
``redis`` → in-memory rate limiter, ``xlsxwriter`` → error response for XLSX only.

Odoo Module Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~

- ``base``
- ``web``
- ``mail``

No Enterprise-only modules required.

Installation
------------

Install via Odoo Apps Store
~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Log in to your Odoo instance as Administrator.
2. Go to **Apps** → search for **MCP Server**.
3. Click **Install**.
4. The module installs with all dependencies automatically.

Install from Source
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Clone or copy the module into your addons path
   cp -r kaitech_mcp_server /path/to/your/addons/

   # Restart Odoo
   ./odoo-bin -c odoo.conf --stop-after-init

   # Install the module
   ./odoo-bin -c odoo.conf -d your_db -i kaitech_mcp_server --stop-after-init

Upgrade
~~~~~~~

.. code-block:: bash

   ./odoo-bin -c odoo.conf -d your_db -u kaitech_mcp_server --stop-after-init

Configuration
-------------

Access the MCP Server settings at **Settings → MCP Server** (Technical section).

General Settings
~~~~~~~~~~~~~~~~

+-------------------------------------+------------------+----------------------------------------------+
| Parameter                           | Default          | Description                                  |
+=====================================+==================+==============================================+
| Session retention (days)            | 90               | Auto-purge sessions older than N days        |
+-------------------------------------+------------------+----------------------------------------------+
| Log retention (days)                | 30               | Auto-purge audit log entries older than N    |
+-------------------------------------+------------------+----------------------------------------------+
| Rate limit per minute               | 60               | Max requests per token per sliding window    |
+-------------------------------------+------------------+----------------------------------------------+
| Rate limit window (seconds)         | 60               | Sliding window duration                      |
+-------------------------------------+------------------+----------------------------------------------+

Redis Rate Limiting (Optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For multi-worker or multi-instance deployments, enable Redis so the rate limit
is shared across all workers:

1. Check **Enable Redis rate limiting**.
2. Enter your Redis URL, e.g. ``redis://localhost:6379/0`` or
   ``redis://:password@redis-host:6379/0``.
3. Click **Test Redis Connection** to verify.

If Redis is unreachable at runtime, the server falls back to in-memory rate
limiting automatically — no downtime.

Schema Cache
~~~~~~~~~~~~

The schema cache stores ``fields_get``, ``get_models``, and ``get_views``
results in memory (LRU, 1000 entries).

1. Check **Enable schema cache**.
2. Set **Cache TTL (seconds)** (default: 3600).
3. Click **Clear Schema Cache** after installing new modules or changing field
   definitions.

The cache is automatically invalidated when ``ir.model`` or ``ir.model.fields``
records change.

Module Generator (Feature Flag)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The AI module generator is disabled by default. To enable:

1. Check **Enable module generator**.
2. Set **Generated modules path** — an absolute path to a writable addons folder
   (e.g. ``/opt/odoo/custom_addons``). The generator refuses to write into
   ``community/`` or ``enterprise/`` directories.
3. Restart Odoo after changing the addons path so the new folder is in scope.

Endpoints
---------

MCP Endpoints
~~~~~~~~~~~~~

+-------------------------------+-----------------------------------------------+
| Endpoint                      | Description                                   |
+===============================+===============================================+
| ``POST /mcp``                 | Streamable HTTP transport (MCP 2025-03-26)    |
+-------------------------------+-----------------------------------------------+
| ``POST /mcp/sse``             | Streamable HTTP alias (backward compatible)   |
+-------------------------------+-----------------------------------------------+
| ``GET  /mcp/sse``             | SSE transport — event stream                  |
+-------------------------------+-----------------------------------------------+
| ``POST /mcp/messages``        | SSE transport — message endpoint              |
+-------------------------------+-----------------------------------------------+

OAuth 2.0 Endpoints
~~~~~~~~~~~~~~~~~~~

+----------------------------------------------+-------------------------------------+
| Endpoint                                     | Description                         |
+==============================================+=====================================+
| ``GET  /.well-known/oauth-authorization-server`` | Auto-discovery metadata         |
+----------------------------------------------+-------------------------------------+
| ``POST /oauth/register``                     | Dynamic client registration (RFC 7591)|
+----------------------------------------------+-------------------------------------+
| ``GET  /oauth/authorize``                    | Authorization Code initiation       |
+----------------------------------------------+-------------------------------------+
| ``POST /oauth/token``                        | Token exchange and refresh          |
+----------------------------------------------+-------------------------------------+
| ``POST /oauth/revoke``                       | Token revocation (RFC 7009)         |
+----------------------------------------------+-------------------------------------+

Portal Endpoints
~~~~~~~~~~~~~~~~

+-------------------------------+-----------------------------------------------+
| Endpoint                      | Description                                   |
+===============================+===============================================+
| ``GET /mcp-page/<slug>``      | Public portal page (HTML)                     |
+-------------------------------+-----------------------------------------------+
| ``GET /mcp-page/<slug>/data`` | Portal page data (JSON)                       |
+-------------------------------+-----------------------------------------------+
| ``GET /mcp-page/<slug>/embed``| Embeddable page (signed token required)       |
+-------------------------------+-----------------------------------------------+

Authentication
--------------

Personal Access Token (PAT)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PATs are the simplest authentication method for CLI tools, scripts, and
server-to-server integrations.

**Generate a PAT:**

1. Go to **MCP Server → Tokens**.
2. Click **Generate PAT**.
3. Copy the token from the dialog — it is shown **only once**.
4. Store it securely (password manager, CI/CD secrets).

**Token properties:**

- SHA-256 hashed at rest; the plain token is never stored
- Valid 30 days by default
- No refresh token; generate a new one when it expires
- Can be revoked at any time from the Tokens list

**Using a PAT in requests:**

.. code-block:: http

   POST /mcp HTTP/1.1
   Host: your-odoo.com
   Authorization: Bearer <your-PAT>
   Content-Type: application/json

OAuth 2.0 Authorization Code + PKCE
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

OAuth 2.0 is used by GUI clients (Claude Web, Claude Desktop, Cursor) that
display a browser login window. It does not require users to generate tokens
manually.

**Flow:**

.. code-block:: text

   1. Client → GET /.well-known/oauth-authorization-server → discover endpoints
   2. Client → POST /oauth/register → get client_id (dynamic registration)
   3. User    → browser → GET /oauth/authorize?code_challenge=S256&...
   4. Odoo    → user logs in → redirect with authorization_code
   5. Client  → POST /oauth/token (code + PKCE verifier) → access_token + refresh_token
   6. Client  → POST /mcp with Bearer access_token

**Token lifetimes:**

- Access token: 30 days
- Refresh token: 90 days (auto-renews the access token transparently)

Token Governance
~~~~~~~~~~~~~~~~

Each token supports fine-grained restrictions, configurable in
**MCP Server → Tokens → [token]**:

+--------------------+-----------------------------------------------------------+
| Setting            | Effect                                                    |
+====================+===========================================================+
| Scope              | ``read`` — read-only tools only                          |
|                    | ``write`` — read + write (default)                        |
|                    | ``admin`` — all tools including module generator          |
+--------------------+-----------------------------------------------------------+
| Model allowlist    | Token can only access listed models                       |
+--------------------+-----------------------------------------------------------+
| Model denylist     | Token cannot access listed models                         |
+--------------------+-----------------------------------------------------------+
| Field allowlist    | JSON per-model field restrictions                         |
+--------------------+-----------------------------------------------------------+
| IP allowlist       | CIDR ranges the token is valid from                       |
+--------------------+-----------------------------------------------------------+
| IP denylist        | CIDR ranges the token is blocked from                     |
+--------------------+-----------------------------------------------------------+
| Capture payloads   | Store full request/response in audit log (PII-redacted)   |
+--------------------+-----------------------------------------------------------+

Connecting AI Clients
---------------------

Claude CLI / Claude Code
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   claude mcp add odoo --transport http https://your-odoo.com/mcp \
     --header "Authorization: Bearer <your-PAT>"

Or via ``~/.claude.json``:

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "type": "http",
         "url": "https://your-odoo.com/mcp",
         "headers": { "Authorization": "Bearer <your-PAT>" }
       }
     }
   }

Claude Desktop
~~~~~~~~~~~~~~

Edit ``claude_desktop_config.json``
(``~/Library/Application Support/Claude/`` on macOS,
``%APPDATA%\Claude\`` on Windows):

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "type": "http",
         "url": "https://your-odoo.com/mcp",
         "headers": { "Authorization": "Bearer <your-PAT>" }
       }
     }
   }

Claude Web (OAuth 2.0)
~~~~~~~~~~~~~~~~~~~~~~

1. Open `claude.ai <https://claude.ai>`_ → **Settings → Integrations → Add MCP Server**.
2. Enter: ``https://your-odoo.com/mcp``
3. Click **Connect** — Claude Web opens a browser window for Odoo login.
4. Authorize the connection. Claude Web handles token storage and renewal.

VSCode Extension
~~~~~~~~~~~~~~~~

Add to ``settings.json``:

.. code-block:: json

   {
     "claude.mcpServers": {
       "odoo": {
         "type": "http",
         "url": "https://your-odoo.com/mcp",
         "headers": { "Authorization": "Bearer <your-PAT>" }
       }
     }
   }

Cursor
~~~~~~

Go to **Cursor Settings → MCP → Add Server**:

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "type": "http",
         "url": "https://your-odoo.com/mcp",
         "headers": { "Authorization": "Bearer <your-PAT>" }
       }
     }
   }

ChatGPT (Connector)
~~~~~~~~~~~~~~~~~~~~

1. Go to **ChatGPT Settings → Connectors → Add MCP Server**.
2. Enter your Odoo MCP URL: ``https://your-odoo.com/mcp``.
3. Enter your Bearer token (PAT or OAuth access token).
4. ChatGPT auto-discovers all tools via ``tools/list``.

Grok
~~~~

1. Go to **Grok Settings → Tools → Add MCP Server**.
2. Enter URL: ``https://your-odoo.com/mcp``.
3. Enter your Bearer token.
4. Confirm connection — Grok lists all available Odoo tools.

Continue.dev
~~~~~~~~~~~~

Edit ``~/.continue/config.json``:

.. code-block:: json

   {
     "experimental": {
       "modelContextProtocolServers": [
         {
           "transport": {
             "type": "sse",
             "url": "https://your-odoo.com/mcp/sse",
             "requestOptions": {
               "headers": { "Authorization": "Bearer <your-PAT>" }
             }
           }
         }
       ]
     }
   }

Windsurf / Void
~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "serverUrl": "https://your-odoo.com/mcp/sse",
         "headers": { "Authorization": "Bearer <your-PAT>" }
       }
     }
   }

n8n
~~~

In n8n, add an **MCP Client Tool** node:

- **Transport:** Streamable HTTP
- **URL:** ``https://your-odoo.com/mcp``
- **Authentication → Header Auth:** ``Authorization: Bearer <your-PAT>``

LangChain (Python)
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from langchain_mcp_adapters.client import MultiServerMCPClient

   async with MultiServerMCPClient({
       "odoo": {
           "url": "https://your-odoo.com/mcp",
           "transport": "streamable_http",
           "headers": {"Authorization": "Bearer <your-PAT>"}
       }
   }) as client:
       tools = client.get_tools()

Vercel AI SDK (TypeScript)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: typescript

   import { experimental_createMCPClient as createMCPClient } from 'ai';

   const mcpClient = await createMCPClient({
     transport: {
       type: 'http',
       url: 'https://your-odoo.com/mcp',
       headers: { Authorization: 'Bearer <your-PAT>' },
     },
   });
   const tools = await mcpClient.tools();

Stdio-Only Clients (Jan, Msty) via mcp-remote
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clients that only support stdio need the ``mcp-remote`` bridge:

.. code-block:: bash

   npm install -g mcp-remote

Then configure:

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "command": "npx",
         "args": [
           "mcp-remote",
           "https://your-odoo.com/mcp/sse",
           "--header",
           "Authorization: Bearer <your-PAT>"
         ]
       }
     }
   }

Production Deployment
---------------------

Nginx Reverse Proxy
~~~~~~~~~~~~~~~~~~~

MCP Server uses HTTP streaming (SSE / chunked transfer) and requires CORS headers
so browser-based AI clients (Claude Web, ChatGPT, etc.) can connect. The config
below is the production-tested reference.

.. code-block:: nginx

   # Upstream — adjust host/port to match your Odoo instance
   upstream odoo_mcp {
       server 127.0.0.1:8069;
       keepalive 32;
   }

   # Restrict CORS to known AI client origins.
   # Add more origins with additional map entries as needed.
   map $http_origin $cors_origin {
       default                      "";
       "https://claude.ai"          $http_origin;
       "https://chatgpt.com"        $http_origin;
       "https://app.grok.com"       $http_origin;
   }

   server {
       listen 443 ssl http2;
       server_name your-odoo.com;

       ssl_certificate     /etc/ssl/your-odoo.com/fullchain.pem;
       ssl_certificate_key /etc/ssl/your-odoo.com/privkey.pem;

       # ── MCP endpoints (SSE + Streamable HTTP) ────────────────────────────
       location ~ ^/mcp {
           # OPTIONS preflight — answer directly from nginx
           if ($request_method = OPTIONS) {
               add_header Access-Control-Allow-Origin  $cors_origin always;
               add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
               add_header Access-Control-Allow-Headers "Content-Type, Authorization, Mcp-Session-Id, Last-Event-ID" always;
               add_header Access-Control-Max-Age       86400 always;
               return 204;
           }

           proxy_pass http://odoo_mcp;
           proxy_http_version 1.1;
           proxy_set_header Connection        "";
           proxy_set_header Host              $host;
           proxy_set_header X-Real-IP         $remote_addr;
           proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;

           # SSE — must disable buffering for streaming to work
           proxy_buffering    off;
           proxy_cache        off;
           proxy_read_timeout 3600s;
           proxy_send_timeout 3600s;
           add_header X-Accel-Buffering no always;

           # CORS response headers
           add_header Access-Control-Allow-Origin  $cors_origin always;
           add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
           add_header Access-Control-Allow-Headers "Content-Type, Authorization, Mcp-Session-Id, Last-Event-ID" always;
       }

       # ── OAuth 2.0 + Well-Known discovery endpoints ────────────────────────
       location ~ ^/(oauth|\.well-known)/ {
           # OPTIONS preflight — required for POST /oauth/register from browser clients
           if ($request_method = OPTIONS) {
               add_header Access-Control-Allow-Origin  $cors_origin always;
               add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
               add_header Access-Control-Allow-Headers "Content-Type, Authorization" always;
               add_header Access-Control-Max-Age       86400 always;
               return 204;
           }

           proxy_pass http://odoo_mcp;
           proxy_http_version 1.1;
           proxy_set_header Connection        "";
           proxy_set_header Host              $host;
           proxy_set_header X-Real-IP         $remote_addr;
           proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;

           # CORS — required so browser-based AI clients can read the response
           add_header Access-Control-Allow-Origin  $cors_origin always;
           add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
           add_header Access-Control-Allow-Headers "Content-Type, Authorization" always;
       }

       # ── Main Odoo application ─────────────────────────────────────────────
       location / {
           proxy_pass http://odoo_mcp;
           proxy_http_version 1.1;
           proxy_set_header Connection        "";
           proxy_set_header Host              $host;
           proxy_set_header X-Real-IP         $remote_addr;
           proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_redirect off;
       }

       gzip on;
       gzip_vary on;
       gzip_types text/css text/javascript application/json application/javascript text/plain text/xml;

       error_page 404 /404.html;
       error_page 500 502 503 504 /50x.html;
   }

.. note::

   **Why ``proxy_read_timeout 3600s``?**
   SSE connections are long-lived. Claude Desktop and other MCP clients keep a
   persistent event stream open for the duration of a session. A short timeout
   (e.g. 300s) causes the client to disconnect and reconnect repeatedly.

.. note::

   **Why separate ``/mcp`` and ``/(oauth|.well-known)/`` locations?**
   The ``/mcp`` location needs ``Mcp-Session-Id`` and ``Last-Event-ID`` in
   ``Access-Control-Allow-Headers``. Keeping OAuth/discovery in its own block
   keeps the CORS header set minimal and avoids leaking internal headers.

Multi-Worker Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~

MCP Server is stateless by design — no sticky sessions required.

**Odoo config (``odoo.conf``):**

.. code-block:: ini

   [options]
   workers = 4
   limit_time_real = 120
   limit_time_cpu = 60

   # For distributed rate limiting and schema cache
   # (add redis-py to requirements first)

**Redis for distributed rate limiting:**

.. code-block:: ini

   # No odoo.conf change needed — configure via Settings → MCP Server
   # Redis URL example:
   # redis://localhost:6379/0
   # redis://:password@redis-cluster:6379/0

Cloudflare / CDN Considerations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If Cloudflare (or another CDN) sits in front of Odoo:

1. **Disable** response buffering on the ``/mcp`` and ``/mcp/sse`` paths.
   In Cloudflare, set a **Cache Rule** with *Bypass Cache* for ``/mcp*``.
2. **Increase** the read timeout to at least 300s on the path
   (Cloudflare Pro+: add a Page Rule with ``SSL = Full (Strict)``
   and ``Origin Response Timeout = 300``).
3. For SSE specifically, ensure Cloudflare does not strip ``Transfer-Encoding: chunked``
   headers — use HTTP/2 between Cloudflare and your origin.

Scheduled Jobs
--------------

Two cron jobs are installed automatically:

+---------------------------------------+------------------+-----------------------------------------------+
| Job                                   | Default interval | Action                                        |
+=======================================+==================+===============================================+
| MCP Server: Purge expired sessions    | Daily            | Deletes sessions older than retention setting |
+---------------------------------------+------------------+-----------------------------------------------+
| MCP Server: Process async job queue   | Every 5 minutes  | Runs pending ``mcp.job`` records              |
+---------------------------------------+------------------+-----------------------------------------------+

Configure cron intervals at **Settings → Technical → Scheduled Actions**.

Maintenance
-----------

Revoking tokens
~~~~~~~~~~~~~~~

Go to **MCP Server → Tokens**, open the token, and click **Revoke**. The token
is rejected on the next request. Active sessions using that token are not
terminated immediately but all subsequent requests fail.

Clearing audit logs
~~~~~~~~~~~~~~~~~~~

Session and log retention is enforced nightly by the purge cron. For an
immediate purge, run from an Odoo shell:

.. code-block:: python

   env['mcp.session'].sudo()._purge_expired()

Clearing the schema cache
~~~~~~~~~~~~~~~~~~~~~~~~~

After installing or upgrading modules:

1. Go to **Settings → MCP Server**.
2. Click **Clear Schema Cache**.

Or from an Odoo shell:

.. code-block:: python

   from odoo.addons.kaitech_mcp_server.services import schema_cache
   schema_cache.invalidate("")

Troubleshooting
---------------

OAuth redirect fails (localhost or HTTP URL)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Odoo's OAuth redirect validation requires HTTPS in production. For local
development, add to ``odoo.conf``:

.. code-block:: ini

   [options]
   proxy_mode = True

And access Odoo through a reverse proxy that sets ``X-Forwarded-Proto: https``,
or use ``ngrok`` / ``localtunnel`` for a temporary HTTPS tunnel.

Rate limit exceeded (HTTP 429)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The default limit is 60 requests per 60 seconds per token. Increase it at
**Settings → MCP Server → Rate limit per minute**, or configure Redis for
shared state across workers.

SSE connection drops after 60 seconds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Nginx ``proxy_read_timeout`` is too short. Set it to 300s on the ``/mcp/sse``
location (see Nginx config above).

MCP client reports "transport error"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Verify the endpoint URL: ``https://your-odoo.com/mcp`` (no trailing slash).
2. Check the ``Authorization`` header is present and the token is not expired.
3. For SSE clients, use ``/mcp/sse`` (not ``/mcp``).
4. Check Nginx is not buffering the response (``proxy_buffering off``).

Extension & Development
-----------------------

Adding Custom Tools
~~~~~~~~~~~~~~~~~~~

Extend the tool registry by inheriting ``mcp.tool.registry``:

.. code-block:: python

   from odoo import api, models

   class MyCustomTools(models.AbstractModel):
       _inherit = "mcp.tool.registry"

       @api.model
       def get_tools(self) -> list:
           base_tools = super().get_tools()
           return base_tools + [
               {
                   "name": "odoo_my_custom_tool",
                   "description": "My custom tool description",
                   "inputSchema": {
                       "type": "object",
                       "properties": {
                           "param": {"type": "string"}
                       },
                       "required": ["param"],
                   },
               }
           ]

Then handle execution by overriding ``tool_executor.execute_tool`` or adding a
handler in your own module.

Module Structure
~~~~~~~~~~~~~~~~

.. code-block:: text

   kaitech_mcp_server/
   ├── __manifest__.py
   ├── controllers/
   │   ├── mcp_controller.py       # POST /mcp, GET/POST /mcp/sse
   │   ├── oauth_controller.py     # OAuth 2.0 endpoints
   │   └── portal_controller.py    # /mcp-page/<slug>
   ├── models/
   │   ├── mcp_session.py          # mcp.session — audit log
   │   ├── mcp_token.py            # mcp.token — PAT + OAuth tokens
   │   ├── mcp_job.py              # mcp.job — async job queue
   │   ├── mcp_portal_page.py      # mcp.portal.page — shareable dashboards
   │   ├── mcp_generated_module.py # mcp.generated.module — AI module builder
   │   ├── mcp_tool_registry.py    # mcp.tool.registry — tool definitions
   │   └── res_config_settings.py  # Settings extension
   └── services/
       ├── mcp_protocol.py         # JSON-RPC envelope, tools/list, tools/call
       ├── tool_executor.py        # Tool dispatch + scope enforcement
       ├── schema_cache.py         # LRU schema cache
       ├── rate_limiter.py         # In-memory rate limiter
       ├── redis_rate_limiter.py   # Redis sliding-window rate limiter
       ├── redaction.py            # PII redaction for payload capture
       ├── bi_tools.py             # BI reporting tools (7 tools)
       ├── job_tools.py            # Async job tools (4 tools)
       ├── portal_tools.py         # Portal page tools (3 tools)
       ├── module_tools.py         # Module generator tools (4 tools)
       └── module_generator.py     # ZIP builder + spec validator
