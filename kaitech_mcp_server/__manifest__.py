# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
{
    'name': 'MCP Server Odoo',
    'version': '19.0.2.0.0',
    'summary': 'MCP Server, AI Integration, Claude, ChatGPT, Gemini, Grok, Cursor, n8n, LangChain, OAuth 2.0, PKCE, Bearer Token, Personal Access Token, REST API, Streamable HTTP, SSE, Async Jobs, Background Queue, BI Reporting, Pivot Table, Time Series, Cohort Analysis, Funnel, Top N, Export CSV, Export XLSX, Portal Pages, Public Dashboard, KPI Tiles, Module Generator, AI Code Generation, LLM, Automation, Audit Log, Rate Limiting, Redis, Schema Cache, Scoped Tokens, Field Allowlist, IP Allowlist, Odoo API, Odoo Connector, AI Assistant, Chatbot, Natural Language, Bulk Update, Bulk Create, Bulk Delete',
    'description': """
MCP Server Odoo
===================

Turn Odoo into an AI-ready tool server. Connect Claude Desktop, ChatGPT, Cursor,
Gemini, n8n, LangChain, and any MCP-capable client to live Odoo data — with
enterprise-grade governance and audit trail.

Core capabilities
-----------------
* OAuth 2.0 Authorization Code Flow with PKCE (S256), dynamic client registration
* Personal Access Tokens (PAT) — self-service
* Token in Authorization: Bearer, X-API-Key or X-Auth-Token; SHA-256 hashed, 30-day expiry, refresh token rotation
* Streamable HTTP transport (MCP 2025-03-26) + legacy SSE transport
* Several databases on one server: /mcp/<database>, the X-Odoo-Database header, or dbfilter
* Auto-discovery via /.well-known/oauth-authorization-server

Governance & audit
------------------
* Per-token scope: read-only, read+write, or admin
* Per-token model allowlist / denylist
* Per-token field allowlist
* Per-token IP allowlist / denylist (CIDR)
* Audit log with optional payload capture (PII-redacted)
* Configurable retention for sessions and logs

Performance
-----------
* Schema cache for fields_get / get_models / get_views (configurable TTL)
* Redis-backed distributed rate limiting (in-memory fallback)
* LRU eviction, automatic invalidation on schema change

Tools (50+)
-----------
* CRUD: get_models, fields_get, search_read, create, write, unlink, call_method
* Workflow: message_post, default_get, onchange, get_views, print_report, create_attachment
* Analytics: count, name_search, read_group
* BI Reporting: pivot, time_series, top_n, cohort, funnel
* Exports: export_csv, export_xlsx
* Async jobs: submit_job, job_status, job_list, job_cancel
* Portal pages: create_portal_page, list_portal_pages, update_portal_page
* Module builder: generate_module, validate_module_spec, list_generated_modules, install_generated_module
* SQL per token and table: sql_list_tables, sql_select, sql_query (checked SELECT), sql_insert, sql_update, sql_delete (dry_run)
* Explore code: describe_model, search_methods, describe_method, get_method_source, search_code, read_source,
  list_modules, describe_module, get_view, list_actions, search_xmlids

Resources
---------
* odoo://context — company, user, modules, server date
* odoo://catalog — all installed models
* odoo://model/{name} — full field schema
* odoo://chatter/{model}/{id} — last 20 chatter messages
* odoo://attachment/{id} — metadata + preview

Portal pages
------------
Public shareable pages with KPI tiles, ECharts visualizations, and tables
backed by live Odoo data. Mobile-responsive, embeddable.

Async job queue
---------------
Submit heavy operations (bulk update, exports across 100k records) and poll
status. Background cron processes jobs respecting token scope and restrictions.

Module generator
----------------
AI generates a JSON spec → validate → ZIP → install. Feature-flagged off
by default. Installing also needs mcp_module_generator = True in the Odoo configuration file.
    """,
    'author': 'kaitech.io',
    'website': 'https://kaitech.io',
    'category': 'Productivity',
    'license': 'OPL-1',
    'depends': ['base', 'web', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/mcp_security.xml',
        'data/mcp_cron.xml',
        'views/mcp_session_views.xml',
        'views/mcp_token_views.xml',
        'views/mcp_job_views.xml',
        'views/mcp_portal_page_views.xml',
        'views/portal_templates.xml',
        'views/mcp_html_artifact_views.xml',
        'views/mcp_generated_module_views.xml',
        'views/res_users_views.xml',
        'views/res_config_settings_views.xml',
        'views/mcp_menus.xml',
    ],
    'external_dependencies': {
        'python': [],
    },
    'post_load': 'post_load',
    'installable': True,
    'application': True,
    'auto_install': False,
    'images': ['static/description/assets/banner.gif', 'static/description/assets/banner.png'],
}
