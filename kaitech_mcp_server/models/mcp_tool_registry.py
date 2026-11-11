from odoo import api, models

from ..services.bi_tools import TOOL_DEFINITIONS as _BI_TOOL_DEFINITIONS
from ..services.job_tools import TOOL_DEFINITIONS as _JOB_TOOL_DEFINITIONS
from ..services.module_tools import TOOL_DEFINITIONS as _MODULE_TOOL_DEFINITIONS
from ..services.portal_tools import TOOL_DEFINITIONS as _PORTAL_TOOL_DEFINITIONS
from ..services.artifact_tools import TOOL_DEFINITIONS as _ARTIFACT_TOOL_DEFINITIONS

# ---------------------------------------------------------------------------
# Static MCP tool definitions (schema advertised to AI clients via tools/list)
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS = [
    {
        "name": "odoo_get_models",
        "description": "List all available Odoo models with their technical name and label",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional keyword to filter model names (e.g. 'sale')",
                }
            },
        },
    },
    {
        "name": "odoo_fields_get",
        "description": "Get field definitions for an Odoo model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name, e.g. 'sale.order'",
                },
                "attributes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attributes to retrieve (default: string, type, required, readonly, help)",
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_search_read",
        "description": "Search and read records from an Odoo model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {
                    "type": "array",
                    "description": "Odoo domain filter, e.g. [['state','=','sale']]",
                    "default": [],
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to read (empty = all fields)",
                },
                "limit": {"type": "integer", "default": 80},
                "offset": {"type": "integer", "default": 0},
                "order": {"type": "string", "description": "e.g. 'name asc, date desc'"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_create",
        "description": "Create a new record in an Odoo model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "values": {"type": "object", "description": "Field values for the new record"},
            },
            "required": ["model", "values"],
        },
    },
    {
        "name": "odoo_write",
        "description": "Update one or more records in an Odoo model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "integer"}},
                "values": {"type": "object"},
            },
            "required": ["model", "ids", "values"],
        },
    },
    {
        "name": "odoo_execute_wizard",
        "description": (
            "Create and execute a wizard (TransientModel) in one atomic call. "
            "Use for confirmation dialogs, e.g. validate a delivery without SMS: "
            "model='confirm.stock.sms', values={'pick_ids': [[4, <picking_id>]]}, "
            "method='dont_send_sms'. "
            "Only works on TransientModels — raises an error for permanent models."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "TransientModel (wizard) name, e.g. 'confirm.stock.sms'",
                },
                "values": {
                    "type": "object",
                    "description": "Field values to initialize the wizard, e.g. {\"pick_ids\": [[4, 12]]}",
                    "default": {},
                },
                "method": {
                    "type": "string",
                    "description": "Public method to call, e.g. 'dont_send_sms', 'action_validate'",
                },
                "kwargs": {
                    "type": "object",
                    "description": "Optional keyword arguments for the method",
                    "default": {},
                },
            },
            "required": ["model", "method"],
        },
    },
    {
        "name": "odoo_unlink",
        "description": "Delete one or more records from an Odoo model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["model", "ids"],
        },
    },
    {
        "name": "odoo_call_method",
        "description": (
            "Call any method on an Odoo model or recordset "
            "(e.g. button_confirm, action_invoice_open)"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "method": {"type": "string", "description": "Method name, e.g. 'button_confirm'"},
                "ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Record IDs (empty = class method)",
                    "default": [],
                },
                "args": {"type": "array", "default": []},
                "kwargs": {"type": "object", "default": {}},
            },
            "required": ["model", "method"],
        },
    },
    # ------------------------------------------------------------------
    # Workflow & form tools
    # ------------------------------------------------------------------
    {
        "name": "odoo_message_post",
        "description": (
            "Post a message to a record's chatter (mail.thread). "
            "Use to send internal notes, notify followers, log activities, "
            "or communicate with customers directly from a record. "
            "Plain text or HTML body accepted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name, e.g. 'sale.order'"},
                "id": {"type": "integer", "description": "Record ID to post the message on"},
                "body": {"type": "string", "description": "Message body — plain text or HTML"},
                "subtype": {
                    "type": "string",
                    "description": "Message subtype XML ID. 'mail.mt_comment' (default) = visible to followers, 'mail.mt_note' = internal note",
                    "default": "mail.mt_comment",
                },
                "partner_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Additional partner IDs to notify (res.partner IDs)",
                    "default": [],
                },
            },
            "required": ["model", "id", "body"],
        },
    },
    {
        "name": "odoo_default_get",
        "description": (
            "Get default field values for a new record before creating it. "
            "Always call this before odoo_create to avoid missing required fields "
            "or wrong defaults (e.g. default journal, company, currency)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field names to get defaults for. Empty = auto-detect all scalar fields.",
                    "default": [],
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_get_views",
        "description": (
            "Discover callable methods, action menu items, window actions, and available reports for a model. "
            "Each callable method includes 'defined_in' showing which class/mixin provides it. "
            "Use before odoo_call_method to know which action_* and button_* methods exist, "
            "or to see what entries appear in the Action menu in the Odoo UI. "
            "Also returns available_reports — use with odoo_print_report."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_create_attachment",
        "description": (
            "Upload a file to Odoo as an ir.attachment record. "
            "Content must be base64-encoded. Can be attached to any Odoo record "
            "(sale order, invoice, contact, etc.) or stored as a standalone document. "
            "After upload, use odoo://attachment/{id} to verify content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Filename including extension, e.g. 'contract.pdf'",
                },
                "content": {
                    "type": "string",
                    "description": "Base64-encoded file content",
                },
                "mimetype": {
                    "type": "string",
                    "description": "MIME type, e.g. 'application/pdf', 'text/csv', 'image/png'",
                    "default": "application/octet-stream",
                },
                "model": {
                    "type": "string",
                    "description": "Technical model name to attach to, e.g. 'sale.order'. Leave empty for standalone.",
                    "default": "",
                },
                "record_id": {
                    "type": "integer",
                    "description": "ID of the record to attach to (required if model is set)",
                    "default": 0,
                },
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "odoo_print_report",
        "description": (
            "Get the download URL for an Odoo PDF/HTML report for one or more records. "
            "Use to generate invoices, delivery slips, sales entries, kontrak rinci, "
            "stock reports, or any Odoo QWeb report. "
            "If 'report' is omitted, the first available PDF report for the model is used "
            "and a list of all available reports is returned so you can choose. "
            "The returned download_url must be opened in a browser with an active Odoo session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name, e.g. 'sale.order', 'account.move'",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Record IDs to include in the report",
                },
                "report": {
                    "type": "string",
                    "description": (
                        "Technical report name, e.g. 'sale.report_saleorder', "
                        "'account.report_invoice'. "
                        "Leave empty to auto-detect and list all available reports."
                    ),
                    "default": "",
                },
            },
            "required": ["model", "ids"],
        },
    },
    {
        "name": "odoo_onchange",
        "description": (
            "Simulate Odoo form onchange — compute dependent field values when a field changes. "
            "Example: set partner_id on a sale.order to get the correct pricelist_id, "
            "payment_term_id, and fiscal_position_id that Odoo would auto-fill. "
            "Pass the new values dict and list the fields that triggered the change. "
            "One2many results are summarized (not expanded) to avoid context overflow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "values": {
                    "type": "object",
                    "description": "Current field values to simulate (include the changed field)",
                },
                "field_onchange": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field names that changed and should trigger their onchange handlers",
                },
            },
            "required": ["model", "values", "field_onchange"],
        },
    },
    # ------------------------------------------------------------------
    # Analytics tools
    # ------------------------------------------------------------------
    {
        "name": "odoo_count",
        "description": (
            "Count records matching a domain without loading them. "
            "Use for quick totals: 'how many unpaid invoices?', 'how many open POs?'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {
                    "type": "array",
                    "description": "Odoo domain filter",
                    "default": [],
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "odoo_name_search",
        "description": (
            "Search records by display name (like an autocomplete). "
            "Use to find a customer, product, or any record by partial name "
            "before creating or linking records."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "name": {"type": "string", "description": "Partial name to search"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["model", "name"],
        },
    },
    {
        "name": "odoo_read_group",
        "description": (
            "Aggregate and group records — the Odoo equivalent of SQL GROUP BY. "
            "Use for business analytics: total sales per customer, stock per warehouse, "
            "invoice amounts per month, etc. "
            "fields syntax: 'field_name' for grouping, 'field_name:sum/count/avg/max/min' for aggregates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name, e.g. 'sale.order'",
                },
                "domain": {
                    "type": "array",
                    "description": "Filter domain, e.g. [['state','=','sale']]",
                    "default": [],
                },
                "groupby": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to group by, e.g. ['partner_id', 'date_order:month']",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Aggregated fields, e.g. ['amount_total:sum', 'id:count']",
                    "default": [],
                },
                "limit": {"type": "integer", "default": 80},
                "orderby": {
                    "type": "string",
                    "description": "Sort order, e.g. 'amount_total desc'",
                },
            },
            "required": ["model", "groupby"],
        },
    },
    {
        "name": "odoo_get_context",
        "description": (
            "Get Odoo business context: company info (name, currency, timezone), "
            "current user details, list of installed modules, and server date. "
            "Use this first to understand the Odoo instance before answering business questions. "
            "Equivalent to reading the odoo://context resource — provided as a tool for "
            "AI clients that do not support MCP Resources (e.g. ChatGPT, Grok)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def get_tool_definitions() -> list:
    """Return the list of MCP tool definitions. Called by mcp_protocol on tools/list."""
    return (
        _TOOL_DEFINITIONS
        + _BI_TOOL_DEFINITIONS
        + _JOB_TOOL_DEFINITIONS
        + _PORTAL_TOOL_DEFINITIONS
        + _ARTIFACT_TOOL_DEFINITIONS
        + _MODULE_TOOL_DEFINITIONS
    )


# ---------------------------------------------------------------------------
# Odoo model — exposes tool registry as an ORM service for extensibility
# ---------------------------------------------------------------------------


class McpToolRegistry(models.AbstractModel):
    _name = "mcp.tool.registry"
    _description = "MCP Tool Registry"

    @api.model
    def get_tools(self) -> list:
        """Return current MCP tool definitions.

        Satellite modules extend the catalog by overriding this method:

            class McpToolRegistry(models.AbstractModel):
                _inherit = "mcp.tool.registry"

                @api.model
                def get_tools(self):
                    return super().get_tools() + MY_TOOL_DEFINITIONS
        """
        return get_tool_definitions()

    @api.model
    def get_tool_handlers(self) -> dict:
        """Return {tool_name: callable(env, args)} for tools registered by satellite
        modules (core tools are dispatched directly by tool_executor and never
        appear here). Override and merge via super() to plug in new handlers:

            @api.model
            def get_tool_handlers(self):
                handlers = super().get_tool_handlers()
                handlers.update(MY_TOOL_HANDLERS)
                return handlers
        """
        return {}

    @api.model
    def get_read_only_tool_names(self) -> frozenset:
        """Tool names (from satellite modules) safe to call under 'read' scope."""
        return frozenset()

    @api.model
    def get_admin_only_tool_names(self) -> frozenset:
        """Tool names (from satellite modules) that require 'admin' scope."""
        return frozenset()

    @api.model
    def get_installed_models(self, filter_kw: str = "") -> list:
        """Return installed non-transient models, optionally filtered by keyword."""
        domain = [("transient", "=", False)]
        if filter_kw:
            domain.append(("model", "ilike", filter_kw))
        return self.env["ir.model"].search_read(
            domain,
            ["model", "name"],
            order="model",
            limit=500,
        )
