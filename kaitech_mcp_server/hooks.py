"""Optional server-wide mode: /mcp/<database> reaches its database on a server without dbfilter."""
import json
import logging
import re
from contextlib import closing

import psycopg2

from odoo import http, sql_db
from odoo.service import db as service_db
from odoo.tools import config

_logger = logging.getLogger(__name__)

MODULE = "kaitech_mcp_server"
DATABASE_PATH = re.compile(r"^/mcp/(?P<db>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:/(?:sse|messages))?/?$")
RESERVED = ("sse", "messages")
NOT_FOUND = json.dumps({"error": "not_found", "error_description": "No MCP endpoint here."}).encode()


def post_load():
    """Route /mcp/<database> by database name, only when the module is listed in server_wide_modules."""
    if not server_wide() or getattr(http.Application, "_kaitech_mcp_server_call", None):
        return
    original_call = http.Application.__call__

    def __call__(self, environ, start_response):
        database = database_of(environ) if server_wide() else None
        if database is None:
            return original_call(self, environ, start_response)
        if not database_serves_mcp(database, host_of(environ)):
            start_response("404 Not Found", [("Content-Type", "application/json"),
                                             ("Content-Length", str(len(NOT_FOUND)))])
            return [NOT_FOUND]
        environ["HTTP_X_ODOO_DATABASE"] = database
        environ.pop("HTTP_COOKIE", None)
        return original_call(self, environ, start_response)

    http.Application._kaitech_mcp_server_call = original_call
    http.Application.__call__ = __call__
    _logger.info("MCP endpoint mounted at /mcp/<database>")


def server_wide():
    return MODULE in config["server_wide_modules"]


def database_of(environ):
    match = DATABASE_PATH.match(environ.get("PATH_INFO") or "")
    if not match or match["db"] in RESERVED:
        return None
    return match["db"]


def host_of(environ):
    if config["proxy_mode"] and environ.get("HTTP_X_FORWARDED_HOST"):
        return environ["HTTP_X_FORWARDED_HOST"]
    return environ.get("HTTP_HOST")


def database_serves_mcp(database, host):
    """True when database exists, passes dbfilter for host, and has the module installed."""
    if database not in service_db.list_dbs(True) or not http.db_filter([database], host=host):
        return False
    try:
        with closing(sql_db.db_connect(database).cursor()) as cr:
            cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (MODULE,))
            row = cr.fetchone()
    except psycopg2.Error:
        return False
    return bool(row) and row[0] == "installed"
