import logging
import threading

_logger = logging.getLogger(__name__)


def post_load():
    """Route /mcp/<db> requests to the MCP endpoint before Odoo's session and database selection."""
    from odoo import http
    from odoo.tools import config

    from . import endpoint

    if getattr(http.Application, '_kaitech_mcp_original_call', None):
        return
    original_call = http.Application.__call__

    def __call__(self, environ, start_response):
        if not endpoint.claims(environ.get('PATH_INFO') or ''):
            return original_call(self, environ, start_response)
        current = threading.current_thread()
        for name in ('dbname', 'uid'):
            if hasattr(current, name):
                delattr(current, name)
        if config['proxy_mode'] and environ.get('HTTP_X_FORWARDED_HOST'):
            http.ProxyFix(lambda environ, start_response: [])(environ, lambda status, headers: None)
        return endpoint.application(environ, start_response)

    http.Application._kaitech_mcp_original_call = original_call
    http.Application.__call__ = __call__
    _logger.info("MCP endpoint mounted at /mcp/<database>")
