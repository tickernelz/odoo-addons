# MCP Server for Odoo — User Guide

> **Version:** 19.0.2.0.0 · **License:** OPL-1 · **Author:** kaitech.io

Turn Odoo into an AI-ready tool server. Connect Claude, ChatGPT, Gemini, Cursor, n8n, LangChain, and any MCP-capable client to live Odoo data — with OAuth 2.0 governance, audit logs, BI tools, portal pages, async jobs, and AI-driven module generation.

> **For installation and connection instructions, see [doc/index.rst](doc/index.rst).**

## Several databases on one server

Without a dbfilter, Odoo picks the database of a request from one of:

- the `X-Odoo-Database: <database>` header on `POST /mcp` (no setting needed);
- `POST /mcp/<database>` when the module is listed in `server_wide_modules` (`--load=base,web,kaitech_mcp_server`);
  an unknown database, one without the module, or one the dbfilter refuses all answer the same 404;
- the host, with `dbfilter = ^%d$`.

`/mcp/<database>`, `/mcp/<database>/sse` and `/mcp/<database>/messages` also work whenever the database is already
selected and the name matches. Odoo refuses to uninstall a module listed in `server_wide_modules`: remove it from
that list and restart before uninstalling.

The token may come as `Authorization: Bearer <token>`, `X-API-Key: <token>` or `X-Auth-Token: <token>`.

## SQL and Explore code

A Settings administrator opens these per token, on the **SQL and Code** tab of the token:

- **Explore code**: `describe_model`, `search_methods`, `describe_method`, `get_method_source`, `search_code`,
  `read_source`, `list_modules`, `describe_module`, `get_view`, `list_actions`, `search_xmlids`.
- **SQL** (also switched on in Settings > MCP Server > SQL): `sql_list_tables`, `sql_select`, `sql_query`
  (one checked SELECT), `sql_insert`, `sql_update`, `sql_delete` (`dry_run` on update and delete), on the tables
  listed on the token with select, insert, update and delete per table. System tables (`ir_*`, `res_users*`,
  `res_groups*`, `mcp_*`, ...) and password, token, secret and binary columns are never reachable, also through
  aliases in a query. Delete needs its own switch in Settings. Read-scope tokens only select.

`odoo_call_method` refuses mail gateway methods and `@api.private` methods for every token, and takes `dry_run`.

## Moving from kaitech_mcp

`kaitech_mcp` keys do not carry over. On each database: note the keys and their permissions, uninstall
`kaitech_mcp` (remove it from `server_wide_modules` and restart first), install `kaitech_mcp_server`, then issue a
token per client (Generate PAT, or a token an administrator creates) and set its scope, model lists, SQL tables and
Explore code to match the old key. Clients keep the `/mcp/<database>` URL when the module is server-wide; the tool
names of the record tools change to `odoo_*` (`search_records` becomes `odoo_search_read`), the SQL and Explore
tool names stay the same.

---

## What Can You Do?

Once connected, your AI assistant can talk to Odoo in plain English:

```
"Show me all unpaid invoices over 30 days old."
"Create a sales order for PT ABC, 5 units of Laptop Pro at 15 million each."
"What is my total revenue by customer this quarter?"
"Generate a PDF for sales order SO/2026/00201."
"Post an internal note on ticket #45 saying the issue is resolved."
```

The AI maps your natural language to the right Odoo models, fields, and methods automatically. You do not need to know technical field names.

---

## Table of Contents

1. [First Steps — Giving Claude Context](#1-first-steps--giving-claude-context)
2. [MCP Resources Reference](#2-mcp-resources-reference)
3. [All 34+ Tools — Quick Reference](#3-all-34-tools--quick-reference)
4. [Business Workflows](#4-business-workflows)
   - [Sales Orders](#41-sales-orders)
   - [Invoicing & Payments](#42-invoicing--payments)
   - [Purchase Orders](#43-purchase-orders)
   - [Inventory & Deliveries](#44-inventory--deliveries)
   - [CRM & Pipeline](#45-crm--pipeline)
5. [Analytics & BI Reporting](#5-analytics--bi-reporting)
6. [Portal Pages (Shareable Dashboards)](#6-portal-pages-shareable-dashboards)
7. [Async Job Queue (Bulk Operations)](#7-async-job-queue-bulk-operations)
8. [AI Module Generator](#8-ai-module-generator)
9. [Chatter & Communication](#9-chatter--communication)
10. [Effective Prompting Tips](#10-effective-prompting-tips)
11. [Troubleshooting for End Users](#11-troubleshooting-for-end-users)

---

## 1. First Steps — Giving Claude Context

When you first connect Claude to Odoo, it does not know your company setup. The first thing to say in every new session:

```
Read odoo://context and odoo://catalog so you understand my Odoo instance before we start.
```

After reading these, Claude knows:
- Your company name, currency, country, timezone
- Which Odoo modules are installed (Sales, Accounting, Inventory, etc.)
- All available data models and their technical names
- Your user name and language

**Recommended session opener:**
```
Read odoo://context and odoo://catalog. Tell me briefly what modules I have installed
and what you can help me with today.
```

---

## 2. MCP Resources Reference

Resources are read-only snapshots Claude loads on demand.

| Resource URI | What it contains | When to use |
|---|---|---|
| `odoo://context` | Company info, user details, installed modules, server date | Start of every session |
| `odoo://catalog` | All installed models with technical names | When exploring what data exists |
| `odoo://model/{name}` | Full field definitions for a model | Before creating or updating records |
| `odoo://chatter/{model}/{id}` | Last 20 chatter messages on a record | Before replying to customers, reviewing history |
| `odoo://attachment/{id}` | File metadata + content preview (text files < 50 KB) | Verifying uploaded files |

**Examples:**
```
Read odoo://model/sale.order — what fields does a Sales Order have?
Read odoo://chatter/helpdesk.ticket/45 — what did the customer say last?
Read odoo://attachment/107 — how many rows are in this CSV?
```

---

## 3. All 34+ Tools — Quick Reference

### CRUD Tools

| Tool | What it does |
|---|---|
| `odoo_get_models` | List all installed Odoo models |
| `odoo_fields_get` | Get field definitions for a model |
| `odoo_search_read` | Search and read records (most-used tool) |
| `odoo_create` | Create a new record |
| `odoo_write` | Update one or more records |
| `odoo_unlink` | Delete records permanently |
| `odoo_call_method` | Call any `action_*` or `button_*` method |

### Workflow & Form Tools

| Tool | What it does |
|---|---|
| `odoo_default_get` | Get default values for a new record |
| `odoo_onchange` | Simulate field-change computed values |
| `odoo_get_views` | Discover callable methods and available reports |
| `odoo_message_post` | Post a chatter message or internal note |
| `odoo_create_attachment` | Upload a file to Odoo |
| `odoo_print_report` | Get the download URL for a PDF/HTML report |

### Analytics Tools

| Tool | What it does |
|---|---|
| `odoo_count` | Count records matching a domain |
| `odoo_name_search` | Find records by display name (autocomplete) |
| `odoo_read_group` | GROUP BY aggregation (SQL GROUP BY equivalent) |

### BI Reporting Tools

| Tool | What it does |
|---|---|
| `odoo_pivot` | Pivot table with row/column/measure grouping |
| `odoo_time_series` | Time-series with gap-filling and trend |
| `odoo_top_n` | Top-N ranking with share % |
| `odoo_cohort` | Cohort retention analysis |
| `odoo_funnel` | Funnel conversion across stages |
| `odoo_export_csv` | Export query results as a CSV download |
| `odoo_export_xlsx` | Export query results as an XLSX download |

### Async Job Tools

| Tool | What it does |
|---|---|
| `odoo_submit_job` | Submit a heavy operation to the background queue |
| `odoo_job_status` | Check the status of a submitted job |
| `odoo_job_list` | List recent jobs |
| `odoo_job_cancel` | Cancel a pending job |

### Portal Page Tools

| Tool | What it does |
|---|---|
| `odoo_create_portal_page` | Create a public shareable dashboard |
| `odoo_list_portal_pages` | List existing portal pages |
| `odoo_update_portal_page` | Update a portal page spec |

### Module Generator Tools *(admin scope required)*

| Tool | What it does |
|---|---|
| `odoo_validate_module_spec` | Validate a module spec without generating |
| `odoo_generate_module` | Generate an installable ZIP from a JSON spec |
| `odoo_list_generated_modules` | List previously generated modules |
| `odoo_install_generated_module` | Install a generated module (with confirmation) |

---

## 4. Business Workflows

### 4.1 Sales Orders

**Create and confirm a sales order:**
```
Create a confirmed sales order for PT Maju Sejahtera for 2 units of Laptop Pro 15
at 15,000,000 each. Add an internal note that delivery is expected by end of month.
```

Claude will automatically:
1. Find the partner ID via `odoo_name_search`
2. Find the product ID via `odoo_name_search`
3. Get defaults via `odoo_default_get` and customer-specific values via `odoo_onchange`
4. Create the sales order + order line
5. Confirm it with `action_confirm`
6. Post the internal note

**Other useful prompts:**
```
Show me all confirmed sales orders this month, sorted by amount descending.
What is the total value of open quotations for customer Acme Corporation?
Print the PDF for sales order SO/2026/00201.
Send a customer-facing message on SO/2026/00201 saying the order is being prepared.
```

---

### 4.2 Invoicing & Payments

**Check overdue invoices:**
```
Show all customer invoices that are more than 30 days overdue.
Include customer name, invoice number, amount, and days overdue.
```

**Post an invoice:**
```
Post (validate) invoice INV/2026/00088.
```

**Check payment status by customer:**
```
How much does Acme Corporation still owe us? List all unpaid invoices.
```

**Reset to draft and correct:**
```
Reset invoice INV/2026/00088 to draft — I need to add a line item.
```

**Aging report:**
```
Show me accounts receivable aging — total owed grouped by whether it's current,
1-30 days overdue, 31-60 days, or 60+ days overdue.
```

---

### 4.3 Purchase Orders

**Create and approve a PO:**
```
Create a purchase order for Supplier ABC for 100 units of Raw Material A at 50,000 each.
Then approve it.
```

**Check pending receipts:**
```
Which purchase orders have been confirmed but not yet received?
```

---

### 4.4 Inventory & Deliveries

**Check stock levels:**
```
What is the current stock level for all products in the main warehouse?
Show anything below 10 units first.
```

**Validate a delivery:**
```
Validate delivery order WH/OUT/00055.
```

**Find products that need replenishment:**
```
Show all products where available quantity is below 5 units.
```

---

### 4.5 CRM & Pipeline

**Review pipeline:**
```
Show all opportunities in the "Proposal" stage worth more than 50 million,
sorted by expected revenue descending.
```

**Move a deal forward:**
```
Move opportunity #77 to stage "Won" and post a note saying the contract was signed today.
```

**Pipeline summary:**
```
Give me a summary of the sales pipeline: total value and count by stage.
```

---

## 5. Analytics & BI Reporting

### read_group — Business aggregation

```
Total sales by customer this year:

odoo_read_group(
  model="sale.order",
  domain=[["state","=","sale"], ["date_order",">=","2026-01-01"]],
  groupby=["partner_id"],
  fields=["amount_total:sum", "id:count"],
  orderby="amount_total desc",
  limit=10
)
```

### Pivot table

```
Show me a pivot table of invoice amounts: rows = customer, columns = month, values = total invoiced.
```

Claude uses `odoo_pivot` to build a crosstab across two dimensions.

### Time-series with trend

```
Show monthly sales revenue for the last 12 months with trend direction.
```

Claude uses `odoo_time_series` with automatic gap-filling for months with no data.

### Top-N ranking

```
Top 10 products by revenue this quarter with share percentage.
```

Claude uses `odoo_top_n` and returns each item's % of total.

### Cohort retention

```
Show me a cohort analysis of new customers acquired in Q1 2026 —
what % are still buying 1 month, 2 months, 3 months later?
```

### Export to CSV/XLSX

```
Export all unpaid invoices over 60 days to an Excel file.
```

Claude uses `odoo_export_xlsx` and returns a download URL for the file.

---

## 6. Portal Pages (Shareable Dashboards)

Portal pages are public URLs backed by live Odoo data — no login required to view.

**Create a KPI dashboard:**
```
Create a portal page at /mcp-page/sales-dashboard with:
- KPI tile: total confirmed sales orders this month
- KPI tile: total revenue this month
- Bar chart: revenue by salesperson this month
- Table: top 10 customers by revenue
```

**Access the page:**
```
https://your-odoo.com/mcp-page/sales-dashboard
```

Pages are mobile-responsive, server-side rendered, and always show current data.
They can be embedded in external tools using a signed URL from `odoo_create_portal_page`.

---

## 7. Async Job Queue (Bulk Operations)

For heavy operations that would time out as a normal request, use the async queue.

**Submit a bulk update:**
```
Submit a background job to update the pricelist for all active customers
in the "Retail" category to "Retail Price List 2026".
```

Claude uses `odoo_submit_job`, returns a `job_id`, and you can poll:

```
Check the status of job #42.
```

**Bulk export:**
```
Submit a background job to export all sales order lines from January 2026 to an XLSX file.
Notify me when it is done.
```

When the job completes, `odoo_job_status` returns a download URL for the file.

---

## 8. AI Module Generator

> **Requires:** admin-scope token + module generator enabled in Settings. Installing also needs
> `mcp_module_generator = True` in the Odoo configuration file (set by the server administrator).

The module generator lets Claude design a full Odoo module from a description —
models, views, security rules, and menus — and produce an installable ZIP.

**Example conversation:**
```
You: Design an Odoo module for managing theme park attractions.
     Each attraction has a name, category (ride/show/restaurant),
     capacity, and maintenance status. Include a kanban view and access groups.

Claude: I'll validate the spec first, then generate the ZIP.
        [uses odoo_validate_module_spec → odoo_generate_module]
        Done. Download: /web/content/42?download=1
```

**Install the generated module:**
```
Install generated module #42. I confirm this is intentional.
```

Claude uses `odoo_install_generated_module` with `confirm: true`.

> **Warning:** Installation restarts the Odoo module registry. Only use in non-production
> environments unless you are certain the generated module is safe.

---

## 9. Chatter & Communication

Every record with a chatter (mail.thread) can be read and posted to.

**Read conversation history:**
```
Read the chatter on sales order SO/2026/00201 and summarize what has happened so far.
```

**Reply to a customer:**
```
On invoice INV/2026/00088, post a customer-facing message:
"Your payment of IDR 25,000,000 is 15 days overdue. Please remit by this Friday."
```

**Internal note:**
```
On helpdesk ticket #45, post an internal note that the issue was escalated to the dev team.
```

| Subtype | Visible to | When to use |
|---|---|---|
| `mail.mt_comment` (default) | Customer + followers | Customer communication, status updates |
| `mail.mt_note` | Internal users only | Team notes, audit trail, process reminders |

---

## 10. Effective Prompting Tips

### Always start with context
```
Read odoo://context and odoo://catalog first, then help me with [task].
```

### Be specific about what you want
```
✅ Show all confirmed sales orders from January 2026, sorted by amount descending,
   with customer name, order date, and total amount.

❌ Show me sales data.
```

### Ask Claude to check before creating
```
Before creating the invoice, read odoo://model/account.move so you know
the required fields and correct defaults.
```

### Chain operations in one prompt
```
Find customer "PT Sentosa", create a quotation for 3 units of Product A at 5,000,000,
add an internal note "requested by sales manager", and confirm the order.
```

### Use read_group for summaries, search_read for details
```
✅ For "how much total?" → use odoo_read_group
✅ For "show me the list" → use odoo_search_read
```

---

## 11. Troubleshooting for End Users

**"Claude doesn't know my Odoo models"**

Run the context loader at the start of every session:
```
Read odoo://context and odoo://catalog.
```

**"Access Denied" error**

Your Odoo user does not have permission for that model or record.
Ask your administrator to check your access rights in
**Settings → Users → [your user] → Access Rights**.

**"Method is blocked"**

The method starts with `_` (private) or is a privilege-escalation method.
Use `odoo_get_views(model=...)` to see which methods are safely callable.

**"Required field missing" when creating records**

Ask Claude:
```
Before creating a [model], use odoo_default_get and odoo_onchange
to check all required fields and defaults first.
```

**"PDF report URL not working"**

You must be logged into Odoo in your browser to access report URLs.
The URL is not publicly accessible.

**"Rate limit exceeded"**

Too many requests per minute. Pause briefly or ask your administrator to
increase the rate limit in **Settings → MCP Server → Rate limit per minute**.
