import logging

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import config as _odoo_config

from ..services import schema_cache

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    mcp_session_retention_days = fields.Integer(
        string="Session Retention (days)",
        config_parameter="mcp_server.session_retention_days",
        default=7,
    )
    mcp_log_retention_days = fields.Integer(
        string="Log Retention (days)",
        config_parameter="mcp_server.log_retention_days",
        default=30,
    )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    mcp_rate_limit_per_minute = fields.Integer(
        string="Rate Limit (requests/min)",
        config_parameter="mcp_server.rate_limit_per_minute",
        default=60,
    )
    mcp_rate_limit_window_seconds = fields.Integer(
        string="Rate Limit Window (seconds)",
        config_parameter="mcp_server.rate_limit_window_seconds",
        default=60,
    )
    mcp_enable_redis_rate_limit = fields.Boolean(
        string="Use Redis for Rate Limiting",
        config_parameter="mcp_server.enable_redis_rate_limit",
    )
    mcp_redis_url = fields.Char(
        string="Redis URL",
        config_parameter="mcp_server.redis_url",
        help="Redis connection URL, e.g. redis://localhost:6379/0. "
             "Only used when 'Use Redis for Rate Limiting' is enabled.",
    )

    # ------------------------------------------------------------------
    # Schema cache (used by other agents — fields defined here so the
    # settings UI is unified in one place)
    # ------------------------------------------------------------------
    mcp_enable_schema_cache = fields.Boolean(
        string="Enable Schema Cache",
        config_parameter="mcp_server.enable_schema_cache",
        default=True,
    )
    mcp_schema_cache_ttl_seconds = fields.Integer(
        string="Schema Cache TTL (seconds)",
        config_parameter="mcp_server.schema_cache_ttl_seconds",
        default=300,
    )

    # ------------------------------------------------------------------
    # Module generator
    # ------------------------------------------------------------------
    mcp_enable_module_generator = fields.Boolean(
        string="Enable Module Generator",
        config_parameter="mcp_server.enable_module_generator",
        help="When enabled, the odoo_generate_module MCP tool is available to admin-scoped "
             "tokens. Disabled by default — enable only on trusted environments.",
    )
    mcp_generated_modules_path = fields.Char(
        string="Generated Modules Path",
        config_parameter="mcp_server.generated_modules_path",
        help="Absolute filesystem path where the install action extracts generated module ZIPs. "
             "Must be a custom addons path registered in Odoo, writable by the Odoo process, "
             "and must NOT be inside community/ or enterprise/ directories.",
    )
    mcp_module_generator_server = fields.Boolean(
        string="Install Allowed on This Server",
        compute="_compute_mcp_module_generator_server",
        help="mcp_module_generator = True in the Odoo configuration file. Without it, generated modules "
             "can be generated and validated but not installed.",
    )

    mcp_sql_enabled = fields.Boolean(
        string="Enable SQL",
        config_parameter="mcp_server.sql_enabled",
        help="Tokens with SQL switched on may use the SQL tools on the tables listed on the token.",
    )
    mcp_sql_delete_enabled = fields.Boolean(
        string="Allow SQL Delete",
        config_parameter="mcp_server.sql_delete_enabled",
    )
    mcp_sql_statement_timeout_ms = fields.Integer(
        string="SQL Statement Timeout (ms)",
        config_parameter="mcp_server.sql_statement_timeout_ms",
        default=5000,
    )
    mcp_sql_forbidden_tables = fields.Char(
        string="Extra Forbidden Tables",
        config_parameter="mcp_server.sql_forbidden_tables",
        help="Comma separated table name patterns no token may use, on top of the system tables.",
    )
    mcp_sql_forbidden_fields = fields.Char(
        string="Extra Forbidden Columns",
        config_parameter="mcp_server.sql_forbidden_fields",
        help="Comma separated column names, or model.column, never readable through SQL.",
    )

    def _compute_mcp_module_generator_server(self):
        enabled = str(_odoo_config.get("mcp_module_generator") or "").strip().lower() in ("1", "true", "yes")
        for settings in self:
            settings.mcp_module_generator_server = enabled

    # ------------------------------------------------------------------
    # Admin actions
    # ------------------------------------------------------------------

    def action_test_redis(self):
        """Ping the configured Redis server and return a client notification."""
        ICP = self.env["ir.config_parameter"].sudo()
        redis_enabled = ICP.get_param("mcp_server.enable_redis_rate_limit", "False")
        if redis_enabled.lower() not in ("1", "true", "yes"):
            raise UserError(_("Redis rate limiting is not enabled. Enable it first."))

        redis_url = ICP.get_param("mcp_server.redis_url", "") or ""
        if not redis_url:
            raise UserError(_("Redis URL is not configured."))

        try:
            from ..services.rate_limiter import get_rate_limiter
            limiter = get_rate_limiter(self.env)

            # If the factory returned the in-memory fallback, Redis is unavailable
            from ..services.rate_limiter import rate_limiter as _mem
            if limiter is _mem:
                raise UserError(
                    _("Could not connect to Redis at %s. Check the URL and that redis-py is installed.") % redis_url
                )

            from ..services.redis_rate_limiter import RedisRateLimiter
            if isinstance(limiter, RedisRateLimiter) and not limiter.ping():
                raise UserError(
                    _("Redis ping failed for %s. The server may be down.") % redis_url
                )

        except UserError:
            raise
        except Exception as exc:
            _logger.exception("MCP Redis test failed")
            raise UserError(_("Redis connection test failed: %s") % exc) from exc

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Redis Connection OK"),
                "message": _("Successfully connected to Redis at %s.") % redis_url,
                "type": "success",
                "sticky": False,
            },
        }

    def action_clear_schema_cache(self):
        """Clear all MCP schema cache entries and report stats."""
        stats_before = schema_cache.get_stats()
        cleared = schema_cache.invalidate()
        _logger.info(
            "MCP schema cache manually cleared by uid=%s: %d entries removed",
            self.env.uid, cleared
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Schema Cache Cleared"),
                "message": _(
                    "Removed %(count)d cache entries. "
                    "Previous hit rate: %(rate)s%%."
                ) % {"count": cleared, "rate": stats_before.get("hit_rate_pct", 0)},
                "type": "success",
                "sticky": False,
            },
        }
