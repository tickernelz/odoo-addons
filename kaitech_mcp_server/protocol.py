# -*- coding: utf-8 -*-
"""MCP over Streamable HTTP without sessions: JSON-RPC parsing, protocol eras, method dispatch."""
import json
import numbers

HANDSHAKE_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
MODERN_VERSIONS = ('2026-07-28',)
SUPPORTED_VERSIONS = MODERN_VERSIONS + HANDSHAKE_VERSIONS

PROTOCOL_VERSION_META = 'io.modelcontextprotocol/protocolVersion'
CLIENT_INFO_META = 'io.modelcontextprotocol/clientInfo'
CLIENT_CAPABILITIES_META = 'io.modelcontextprotocol/clientCapabilities'
SERVER_INFO_META = 'io.modelcontextprotocol/serverInfo'

VERSION_HEADER = 'Mcp-Protocol-Version'
METHOD_HEADER = 'Mcp-Method'
NAME_HEADER = 'Mcp-Name'

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
REFUSED_HERE = -32001
RESOURCE_NOT_FOUND = -32002
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
OVER_THE_LIMIT = -32029

MODERN_ERROR_STATUS = {
    PARSE_ERROR: 400, INVALID_REQUEST: 400, INVALID_PARAMS: 400, HEADER_MISMATCH: 400,
    UNSUPPORTED_PROTOCOL_VERSION: 400, METHOD_NOT_FOUND: 404,
}

SERVER_INFO = {'name': 'kaitech-odoo10-mcp', 'title': 'Odoo 10 (Kaitech MCP Server)', 'version': '10.0.1.0.0'}
CAPABILITIES = {'tools': {'listChanged': False}}
EXPLORE_CAPABILITIES = {'tools': {'listChanged': False}, 'resources': {'listChanged': False}}
INSTRUCTIONS = (
    "This server exposes one Odoo 10 database. Every call runs as the Odoo user bound to your key, "
    "limited to the models and actions the key allows. Start with list_models, then get_model_fields "
    "before searching or writing. Odoo business errors come back as tool results with isError true; "
    "read the message and adjust the arguments."
)
EXPLORE_INSTRUCTIONS = (
    " To understand the code, in this order: list_modules (author tells custom modules from Odoo's); "
    "describe_model (modules, fields with compute and depends, methods); search_fields (which models have a "
    "field or point to a model); search_methods or describe_method "
    "(override order across modules); get_method_source (code with file and line). search_code and read_source "
    "reach any source file of an installed module; get_view and list_actions show the screens and menus users see. "
    "When has_more is true, call again with next_offset. The resource odoo://overview summarises this database."
)
METHOD_INSTRUCTIONS = (
    " call_method runs a public business method as your key's user; run it with dry_run true first "
    "to see its result and the changed fields without saving anything."
)


def capabilities(key):
    return EXPLORE_CAPABILITIES if key.get('allow_explore') else CAPABILITIES


def instructions(key):
    """The initialize text: the base rules, plus how to explore and call methods when the key may."""
    text = INSTRUCTIONS
    if key.get('allow_explore'):
        text += EXPLORE_INSTRUCTIONS
    if key.get('allow_methods'):
        text += METHOD_INSTRUCTIONS
    return text

UNAUDITED_METHODS = ('ping',)


class Rejection(Exception):
    """A JSON-RPC error answer for one request."""

    def __init__(self, code, message, data=None, status=None):
        super(Rejection, self).__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.status = status


def parse(body):
    """Decode one JSON-RPC 2.0 message; raise Rejection with HTTP 400 otherwise."""
    try:
        message = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise Rejection(PARSE_ERROR, "The body is not JSON.", status=400)
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), basestring):
        raise Rejection(INVALID_REQUEST, "The body is not one JSON-RPC 2.0 request.", status=400)
    if 'id' in message and not _valid_id(message['id']):
        raise Rejection(INVALID_REQUEST, "The request id must be a string or a number.", status=400)
    params = message.get('params')
    if params is not None and not isinstance(params, dict):
        raise Rejection(INVALID_REQUEST, "params must be an object.", status=400)
    return message


def _valid_id(value):
    return isinstance(value, basestring) or (isinstance(value, numbers.Number) and not isinstance(value, bool))


def is_notification(message):
    return 'id' not in message


def is_modern(message):
    params = message.get('params') or {}
    meta = params.get('_meta')
    return message.get('method') == 'server/discover' or (isinstance(meta, dict) and PROTOCOL_VERSION_META in meta)


def client_info(message):
    params = message.get('params') or {}
    meta = params.get('_meta')
    info = meta.get(CLIENT_INFO_META) if isinstance(meta, dict) else None
    if not isinstance(info, dict):
        info = params.get('clientInfo') if message.get('method') == 'initialize' else None
    if not isinstance(info, dict):
        return None, None
    name, version = info.get('name'), info.get('version')
    return (name if isinstance(name, basestring) else None, version if isinstance(version, basestring) else None)


def requested_version(message):
    params = message.get('params') or {}
    if is_modern(message):
        meta = params.get('_meta')
        value = meta.get(PROTOCOL_VERSION_META) if isinstance(meta, dict) else None
    else:
        value = params.get('protocolVersion') if message.get('method') == 'initialize' else None
    return value if isinstance(value, basestring) else None


def check_modern_envelope(message, headers):
    """The 2026-07-28 per-request ladder: envelope keys, mirrored headers, supported version."""
    params = message.get('params') or {}
    meta = params.get('_meta')
    if not isinstance(meta, dict):
        raise Rejection(INVALID_PARAMS, "params._meta must be an object carrying %r and %r."
                        % (PROTOCOL_VERSION_META, CLIENT_CAPABILITIES_META))
    missing = [key for key in (PROTOCOL_VERSION_META, CLIENT_CAPABILITIES_META) if key not in meta]
    if missing:
        raise Rejection(INVALID_PARAMS, "params._meta is missing: %s" % ', '.join(missing))
    version = meta[PROTOCOL_VERSION_META]
    if headers.get(VERSION_HEADER) is None or headers.get(VERSION_HEADER) != version:
        raise Rejection(HEADER_MISMATCH, "%s header does not match the request envelope's protocol version" % VERSION_HEADER)
    if headers.get(METHOD_HEADER) != message['method']:
        raise Rejection(HEADER_MISMATCH, "%s header does not match the request body's method" % METHOD_HEADER)
    if message['method'] == 'tools/call' and params.get('name') is not None and headers.get(NAME_HEADER) != params.get('name'):
        raise Rejection(HEADER_MISMATCH, "%s header does not match the request body's 'name' parameter" % NAME_HEADER)
    if not isinstance(version, basestring):
        raise Rejection(INVALID_PARAMS, "the protocol-version envelope value must be a string")
    if version not in MODERN_VERSIONS:
        raise Rejection(UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version",
                        data={'supported': list(MODERN_VERSIONS), 'requested': version})
    return version


def negotiate(message):
    asked = (message.get('params') or {}).get('protocolVersion')
    return asked if asked in HANDSHAKE_VERSIONS else HANDSHAKE_VERSIONS[0]


def finish(result, modern):
    """Stamp the 2026-07-28 result fields; handshake-era results stay as they are."""
    if modern:
        result.setdefault('resultType', 'complete')
        meta = dict(result.get('_meta') or {})
        meta.setdefault(SERVER_INFO_META, dict(SERVER_INFO))
        result['_meta'] = meta
    return result


def answer(ident, result):
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def error(ident, code, message, data=None):
    body = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}}
    if data is not None:
        body['error']['data'] = data
    return body
