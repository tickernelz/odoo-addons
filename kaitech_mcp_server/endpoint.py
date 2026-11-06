# -*- coding: utf-8 -*-
"""WSGI handler for POST /mcp/<database>: database choice, key check, rate limit, audit, dispatch."""
import json
import logging
import re
import threading
import time
import urlparse
from contextlib import closing

import psycopg2
from psycopg2.extensions import TransactionRollbackError
import werkzeug.wrappers

import odoo
from odoo import api
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError, ValidationError, except_orm

from . import grants, keys, protocol, tools

_logger = logging.getLogger(__name__)

MODULE = 'kaitech_mcp_server'
PREFIX = '/mcp/'
MAX_BODY = 1024 * 1024
DB_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
LOCK_SPACE = 0x4d43
WINDOW_SECONDS = 60
TRIES = 5

NOT_FOUND_BODY = json.dumps(protocol.error(None, protocol.REFUSED_HERE, "No MCP endpoint here."))
NO_KEY = "A valid MCP key is required as a Bearer token."
FOREIGN_ORIGIN = "Requests from another site's pages are refused."
TOO_LARGE = "The body is larger than 1 MB."
OVER_LIMIT = "Too many calls for this key; try again in %s seconds."
TOOL_FAILED = "The tool failed; nothing was changed."


def claims(path):
    return path == '/mcp' or path.startswith(PREFIX)


def server_wide():
    return MODULE in odoo.conf.server_wide_modules


def application(environ, start_response):
    request = werkzeug.wrappers.Request(environ)
    try:
        response = _answer(request)
    except Exception:
        _logger.exception("MCP request failed")
        response = _json(500, protocol.error(None, protocol.INTERNAL_ERROR, "Internal error."))
    return response(environ, start_response)


def _json(status, body, headers=None):
    payload = body if isinstance(body, basestring) else json.dumps(body, ensure_ascii=False, default=tools._plain)
    if isinstance(payload, unicode):
        payload = payload.encode('utf-8')
    response = werkzeug.wrappers.Response(payload, status=status, mimetype='application/json')
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def _not_found():
    return _json(404, NOT_FOUND_BODY)


def database_of(path):
    if not path.startswith(PREFIX):
        return None
    name = path[len(PREFIX):].rstrip('/')
    if not name or '/' in name or not DB_NAME.match(name):
        return None
    return name


def database_available(db, httprequest):
    """True when db exists for this server, passes dbfilter for this host, and has the module installed."""
    if not server_wide():
        return False
    if db not in odoo.service.db.list_dbs(True):
        return False
    if db not in odoo.http.db_filter([db], httprequest=httprequest):
        return False
    try:
        with closing(odoo.sql_db.db_connect(db).cursor()) as cr:
            cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (MODULE,))
            row = cr.fetchone()
    except psycopg2.Error:
        return False
    return bool(row) and row[0] == 'installed'


def _foreign_origin(request):
    origin = request.headers.get('Origin')
    if not origin:
        return False
    if origin == 'null':
        return True
    return urlparse.urlparse(origin).netloc.lower() != (request.host or '').lower()


def _body(request):
    length = request.content_length
    if length is not None and length > MAX_BODY:
        return None
    data = request.stream.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        return None
    return data


def _bearer(request):
    scheme, _space, token = (request.headers.get('Authorization') or '').partition(' ')
    return token.strip() if scheme.lower() == 'bearer' else ''


def _answer(request):
    if request.method != 'POST':
        return _json(405, protocol.error(None, protocol.INVALID_REQUEST, "Only POST is served here."), {'Allow': 'POST'})
    db = database_of(request.path)
    if db is None or not database_available(db, request):
        return _not_found()
    if _foreign_origin(request):
        return _json(403, protocol.error(None, protocol.REFUSED_HERE, FOREIGN_ORIGIN))
    body = _body(request)
    if body is None:
        return _json(413, protocol.error(None, protocol.INVALID_REQUEST, TOO_LARGE))
    try:
        message = protocol.parse(body)
    except protocol.Rejection as rejection:
        return _json(rejection.status, protocol.error(None, rejection.code, rejection.message))
    current = threading.current_thread()
    saved = dict((name, getattr(current, name)) for name in ('dbname', 'uid') if hasattr(current, name))
    with api.Environment.manage():
        registry = odoo.registry(db).check_signaling()
        current.dbname = db
        try:
            return Exchange(registry, request, message).serve()
        finally:
            registry.signal_caches_change()
            for name in ('dbname', 'uid'):
                if name in saved:
                    setattr(current, name, saved[name])
                elif hasattr(current, name):
                    delattr(current, name)


class Exchange(object):
    """One authenticated JSON-RPC request against one database."""

    def __init__(self, registry, request, message):
        self.registry = registry
        self.request = request
        self.message = message
        self.ident = message.get('id')
        self.method = message['method']
        self.params = message.get('params') or {}
        self.modern = protocol.is_modern(message)
        self.started = time.time()
        self.call_id = None
        self.tool = self.params.get('name') if self.method == 'tools/call' else None
        if not isinstance(self.tool, basestring):
            self.tool = None

    def serve(self):
        key, refusal = self._authenticate()
        if key is None:
            return self._unauthorized(refusal)
        if refusal:
            self._audit_refusal(key, refusal)
            return self._unauthorized(refusal)
        if protocol.is_notification(self.message):
            return werkzeug.wrappers.Response(status=202)
        if self.method not in protocol.UNAUDITED_METHODS:
            wait = self._reserve(key)
            if wait:
                return self._error(protocol.OVER_THE_LIMIT, OVER_LIMIT % wait, status=429, headers={'Retry-After': str(wait)})
        try:
            version = protocol.check_modern_envelope(self.message, self.request.headers) if self.modern else None
            return self._dispatch(key, version)
        except protocol.Rejection as rejection:
            self._finish('invalid_arguments', rejection.message)
            status = rejection.status or (protocol.MODERN_ERROR_STATUS.get(rejection.code, 200) if self.modern else 200)
            return self._error(rejection.code, rejection.message, data=rejection.data, status=status)

    def _error(self, code, message, data=None, status=200, headers=None):
        return _json(status, protocol.error(self.ident, code, message, data), headers)

    def _unauthorized(self, refusal):
        _logger.info("MCP key refused on %s from %s: %s", self.registry.db_name, self.request.remote_addr, refusal or 'missing')
        return self._error(protocol.REFUSED_HERE, NO_KEY, status=401, headers={'WWW-Authenticate': 'Bearer realm="mcp"'})

    def _authenticate(self):
        token = _bearer(self.request)
        index = keys.index_of(token)
        if not index:
            return None, 'missing'
        with closing(self.registry.cursor()) as cr:
            cr.execute("""
                SELECT k.id, k.name, k.key_hash, k.user_id, k.rate_per_minute, k.writes_per_minute,
                       k.max_read, k.max_write, k.max_delete, k.allow_sql,
                       EXISTS(SELECT 1 FROM res_groups_users_rel r JOIN ir_model_data d
                                  ON d.res_id = r.gid AND d.model = 'res.groups'
                                 AND d.module = 'base' AND d.name = 'group_system'
                               WHERE r.uid = k.user_id) AS is_system,
                       k.revoked_at IS NOT NULL AS revoked,
                       (k.expires_at IS NOT NULL AND k.expires_at <= (now() AT TIME ZONE 'UTC')) AS expired,
                       u.active AS user_active
                  FROM kaitech_mcp_key k JOIN res_users u ON u.id = k.user_id
                 WHERE k.key_index = %s""", (index,))
            rows = cr.dictfetchall()
        key = None
        for row in rows:
            if keys.matches(token, row['key_hash']):
                key = row
                break
        if key is None:
            return None, 'unknown'
        if key['revoked']:
            return key, 'revoked'
        if key['expired']:
            return key, 'expired'
        if key['user_id'] == odoo.SUPERUSER_ID:
            return key, 'superuser'
        if not key['user_active']:
            return key, 'user_inactive'
        return key, None

    def _client(self):
        name, version = protocol.client_info(self.message)
        if not name:
            name = (self.request.headers.get('User-Agent') or '')[:64] or None
        return name, version

    def _insert(self, cr, key, result, reason):
        name, version = self._client()
        cr.execute("""
            INSERT INTO kaitech_mcp_call
                (key_id, key_name, user_id, method, tool, is_write, result, reason, client_name, client_version,
                 remote_addr, protocol_version, duration_ms, record_count,
                 create_uid, create_date, write_uid, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,
                    %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC')
            RETURNING id""", (
            key['id'], key['name'], key['user_id'], self.method, self.tool,
            self.tool in tools.WRITE_TOOLS, result, reason, name and name[:64], version and version[:32],
            self.request.remote_addr, protocol.requested_version(self.message), key['user_id'], key['user_id']))
        return cr.fetchone()[0]

    def _audit_refusal(self, key, refusal):
        if not protocol.is_notification(self.message) and self.method not in protocol.UNAUDITED_METHODS:
            if self._reserve(key, refused=refusal) is None:
                self._finish('refused', refusal)

    def _reserve(self, key, refused=None):
        """Count this call against the key's limits; return seconds to wait when over a limit."""
        with closing(self.registry.cursor()) as cr:
            cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_SPACE, key['id']))
            cr.execute("""
                SELECT coalesce(sum(CASE WHEN result != 'limited' THEN 1 ELSE 0 END), 0),
                       coalesce(sum(CASE WHEN result != 'limited' AND is_write THEN 1 ELSE 0 END), 0),
                       coalesce(sum(CASE WHEN result = 'limited' THEN 1 ELSE 0 END), 0),
                       ceil(extract(epoch FROM min(create_date) - ((now() AT TIME ZONE 'UTC') - interval '%s seconds')))
                  FROM kaitech_mcp_call
                 WHERE key_id = %%s AND create_date > (now() AT TIME ZONE 'UTC') - interval '%s seconds'
            """ % (WINDOW_SECONDS, WINDOW_SECONDS), (key['id'],))
            calls, writes, limited, wait = cr.fetchone()
            over = calls >= (key['rate_per_minute'] or grants.DEFAULTS['default_rate_per_minute'])
            if self.tool in tools.WRITE_TOOLS:
                over = over or writes >= (key['writes_per_minute'] or grants.DEFAULTS['default_writes_per_minute'])
            if over:
                if not limited:
                    self._insert(cr, key, 'limited', 'rate_limit')
                cr.commit()
                return max(1, int(wait or 1))
            self.call_id = self._insert(cr, key, 'error', refused or 'interrupted')
            cr.commit()
        return None

    def _finish(self, result, reason=None, outcome=None, statement=None):
        if not self.call_id:
            return
        values = [result, (reason or '')[:256] or None, int((time.time() - self.started) * 1000)]
        model = ids = written = deleted = None
        count = 0
        dry_run = False
        if outcome is not None:
            model = outcome.model
            statement = outcome.statement
            dry_run = outcome.dry_run
            ids = ','.join(_key_text(item) for item in outcome.ids[:50]) or None
            if len(outcome.ids) > 50:
                ids += ',...'
            count = outcome.count
            written = ','.join(outcome.written)[:1024] or None
            if self.tool == 'delete_records':
                deleted = '\n'.join('%s %s' % (item['id'], item['display_name']) for item in outcome.payload['deleted'])
        with closing(self.registry.cursor()) as cr:
            cr.execute("""
                UPDATE kaitech_mcp_call
                   SET result = %s, reason = %s, duration_ms = %s, model = coalesce(%s, model), record_ids = %s,
                       record_count = %s, fields_written = %s, deleted_names = %s, statement = %s, dry_run = %s,
                       write_date = now() AT TIME ZONE 'UTC'
                 WHERE id = %s""", values + [model, ids, count, written, deleted,
                                            statement and statement[:4000], dry_run, self.call_id])
            cr.commit()

    def _dispatch(self, key, version):
        method = self.method
        if method == 'ping':
            return _json(200, protocol.answer(self.ident, protocol.finish({}, self.modern)))
        if method == 'initialize':
            if self.modern:
                raise protocol.Rejection(protocol.UNSUPPORTED_PROTOCOL_VERSION, "initialize is not part of %s" % version,
                                         data={'supported': list(protocol.MODERN_VERSIONS),
                                               'requested': protocol.requested_version(self.message) or ''})
            result = {'protocolVersion': protocol.negotiate(self.message), 'capabilities': protocol.CAPABILITIES,
                      'serverInfo': protocol.SERVER_INFO, 'instructions': protocol.INSTRUCTIONS}
            self._finish('ok')
            return _json(200, protocol.answer(self.ident, result))
        if method == 'server/discover':
            result = {'supportedVersions': list(protocol.SUPPORTED_VERSIONS), 'capabilities': protocol.CAPABILITIES,
                      'instructions': protocol.INSTRUCTIONS, 'ttlMs': 0, 'cacheScope': 'private'}
            self._finish('ok')
            return _json(200, protocol.answer(self.ident, protocol.finish(result, True)))
        if method == 'tools/list':
            with self._environment(key) as (env, grant):
                listed = tools.visible(env, grant)
            self._finish('ok')
            result = {'tools': listed}
            if self.modern:
                result.update({'ttlMs': 0, 'cacheScope': 'private'})
            return _json(200, protocol.answer(self.ident, protocol.finish(result, self.modern)))
        if method == 'tools/call':
            return self._call_tool(key)
        raise protocol.Rejection(protocol.METHOD_NOT_FOUND, "Method %s is not offered here." % method)

    def _environment(self, key):
        exchange = self

        class Scope(object):
            def __enter__(self):
                self.cr = exchange.registry.cursor()
                threading.current_thread().uid = key['user_id']
                env = api.Environment(self.cr, key['user_id'], {})
                env = env(context=dict(env['res.users'].context_get()))
                return env, exchange._grant(self.cr, key)

            def __exit__(self, kind, value, trace):
                try:
                    if kind is None:
                        self.cr.commit()
                    else:
                        self.cr.rollback()
                finally:
                    self.cr.close()
                return False

        return Scope()

    def _grant(self, cr, key):
        cr.execute("""
            SELECT p.id, m.model, p.can_read, p.can_create, p.can_update, p.can_delete
              FROM kaitech_mcp_permission p JOIN ir_model m ON m.id = p.model_id
             WHERE p.key_id = %s""", (key['id'],))
        rows = {}
        by_id = {}
        for permission_id, model, can_read, can_create, can_update, can_delete in cr.fetchall():
            entry = {'read': can_read, 'create': can_create, 'update': can_update, 'delete': can_delete, 'fields': None}
            rows[model] = entry
            by_id[permission_id] = entry
        if by_id:
            cr.execute("""
                SELECT r.permission_id, f.name FROM kaitech_mcp_permission_field_rel r
                  JOIN ir_model_fields f ON f.id = r.field_id WHERE r.permission_id IN %s""", (tuple(by_id),))
            for permission_id, name in cr.fetchall():
                entry = by_id[permission_id]
                entry['fields'] = (entry['fields'] or set()) | set([name])
        cr.execute("""
            SELECT table_name, can_select, can_insert, can_update, can_delete
              FROM kaitech_mcp_sql_table WHERE key_id = %s""", (key['id'],))
        tables = dict((name, {'select': can_select, 'insert': can_insert, 'update': can_update, 'delete': can_delete})
                      for name, can_select, can_insert, can_update, can_delete in cr.fetchall())
        return grants.Grant(key, rows, grants.read_settings(cr), tables)

    def _call_tool(self, key):
        arguments = self.params.get('arguments')
        if arguments is None:
            arguments = {}
        for attempt in range(TRIES):
            try:
                with self._environment(key) as (env, grant):
                    outcome = tools.call(env, grant, self.tool, arguments)
                    payload = tools.dumps(outcome.payload)
                self._finish('ok', outcome=outcome)
                return self._tool_result(outcome.payload, payload, False)
            except TransactionRollbackError:
                if attempt == TRIES - 1:
                    self._finish('error', 'serialization_failure')
                    return self._tool_failure("The database was busy; nothing was changed. Try again.")
                time.sleep(0.05 * (attempt + 1))
            except grants.ToolArgumentError as failure:
                self._finish('invalid_arguments', '%s' % failure, statement=getattr(failure, 'statement', None))
                return self._tool_failure('%s' % failure)
            except (grants.ToolRefused, AccessError, AccessDenied) as failure:
                text = _odoo_message(failure)
                self._finish('refused', text, statement=getattr(failure, 'statement', None))
                return self._tool_failure(text)
            except (UserError, ValidationError, MissingError, except_orm) as failure:
                text = _odoo_message(failure)
                self._finish('error', text)
                return self._tool_failure(text)
            except psycopg2.Error as failure:
                text = "The database refused this: %s" % ((failure.pgerror or '%s' % failure).strip().splitlines() or [''])[0]
                self._finish('error', text, statement=getattr(failure, 'statement', None))
                return self._tool_failure(text)
            except Exception:
                _logger.exception("MCP tool %s failed on %s", self.tool, self.registry.db_name)
                self._finish('error', 'exception')
                return self._tool_failure(TOOL_FAILED)

    def _tool_failure(self, text):
        return self._tool_result({'error': text}, tools.dumps({'error': text}), True)

    def _tool_result(self, structured, text, failed):
        result = {'content': [{'type': 'text', 'text': text}], 'structuredContent': structured, 'isError': failed}
        return _json(200, protocol.answer(self.ident, protocol.finish(result, self.modern)))


def _key_text(value):
    if isinstance(value, (list, tuple)):
        return '(%s)' % ','.join('%s' % item for item in value)
    return '%s' % (value,)


def _odoo_message(failure):
    for attribute in ('value', 'name'):
        text = getattr(failure, attribute, None)
        if text and isinstance(text, basestring) and text not in ('Access Error', 'Access Denied'):
            return text
    return '%s' % (failure,) or failure.__class__.__name__
