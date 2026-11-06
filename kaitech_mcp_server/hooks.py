# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def post_load():
    """Route /mcp/<db> requests to the MCP endpoint before Odoo's session and database selection."""
    from odoo import http
    from . import endpoint

    if getattr(http.Root, '_kaitech_mcp_original_call', None):
        return
    original_call = http.Root.__call__

    def __call__(self, environ, start_response):
        if endpoint.claims(environ.get('PATH_INFO') or ''):
            return endpoint.application(environ, start_response)
        return original_call(self, environ, start_response)

    http.Root._kaitech_mcp_original_call = original_call
    http.Root.__call__ = __call__
    _logger.info("MCP endpoint mounted at /mcp/<database>")
