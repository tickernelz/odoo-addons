# -*- coding: utf-8 -*-
"""The read-only explore tools: models, methods, source, modules, views, actions and XML ids."""
import fnmatch
import inspect
import os
import re
import subprocess
import threading

from lxml import etree

import odoo
from odoo.modules import module as odoo_module

from . import explore
from .grants import ACTIONS, ToolArgumentError, ToolRefused, flag, integer

_READ_ONLY = {'readOnlyHint': True, 'openWorldHint': False}
_MODEL = {'type': 'string', 'description': 'Technical model name, for example sale.order.'}
_METHOD = {'type': 'string', 'description': 'Method name, for example action_confirm.'}
_LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50}
_OFFSET = {'type': 'integer', 'minimum': 0, 'default': 0}
_START = {'type': 'integer', 'minimum': 1, 'default': 1}
VIEW_TYPES = ('form', 'tree', 'list', 'kanban', 'search', 'pivot', 'graph', 'calendar', 'gantt')
DEFAULT_FILE_TYPES = ('py', 'xml', 'js', 'csv')
MODULE_STATES = ('installed', 'uninstalled', 'to upgrade', 'to install', 'all')
MAX_PATTERN = 200
SEARCH_SECONDS = 15
MAX_SOURCE_LINES = 1000
MAX_READ_LINES = 500
MAX_LINE_CHARS = 400
MATCH_CHARS = 240
DOC_CHARS = 800


def _tool(name, title, description, properties, required=()):
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        schema['required'] = list(required)
    return {'name': name, 'title': title, 'action': 'explore', 'description': description,
            'inputSchema': schema, 'annotations': _READ_ONLY}


DEFINITIONS = (
    _tool('describe_model', 'Describe a model',
          "Understand one model in one call: defining and extending modules, parent models, fields (type, relation, "
          "compute, depends, related, module), its own methods, methods inherited per parent, and what this key may do.",
          {'model': _MODEL, 'fields': {'type': 'array', 'items': {'type': 'string'},
                                      'description': 'Only these fields. Default: every field.'},
           'query': {'type': 'string', 'description': 'Keep fields and methods whose name contains this.'}},
          ('model',)),
    _tool('search_methods', 'Search methods',
          "Find methods whose name contains query, on one model (own and inherited) or across models (where a model "
          "defines or overrides it). modules lists the overriding modules, outermost first; the last one defines it.",
          {'query': {'type': 'string', 'description': 'Part of a method name, for example confirm.'},
           'model': dict(_MODEL, description='Only this model.'), 'limit': _LIMIT, 'offset': _OFFSET}, ('query',)),
    _tool('describe_method', 'Describe a method',
          "Signature, decorators, docstring, the fields it computes, checks or reacts to, and every override "
          "(module, file, line, whether it calls super), outermost first.",
          {'model': _MODEL, 'method': _METHOD}, ('model', 'method')),
    _tool('get_method_source', 'Get method source',
          "Source of a method and of every override, outermost first, with file and first line. "
          "Framework code under odoo/ is located but not included.",
          {'model': _MODEL, 'method': _METHOD,
           'module': {'type': 'string', 'description': 'Only the definition in this module.'},
           'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': MAX_SOURCE_LINES, 'default': 300}},
          ('model', 'method')),
    _tool('search_code', 'Search source code',
          "Search the source files of the installed modules (or one module). Returns file, line and the line itself; "
          "read more with read_source. regex is POSIX extended (grep -E, no backreferences) and stops after %d seconds." % SEARCH_SECONDS,
          {'query': {'type': 'string', 'description': 'Text to find, at least 2 characters; a regular expression '
                                                      'when regex is true.'},
           'module': {'type': 'string', 'description': 'Only this installed module.'},
           'file_types': {'type': 'array', 'items': {'type': 'string', 'enum': list(explore.SOURCE_TYPES)},
                          'default': list(DEFAULT_FILE_TYPES), 'description': 'Default: py, xml, js, csv.'},
           'path': {'type': 'string', 'description': "Glob on module/relative/path, for example '*/models/*.py'."},
           'regex': {'type': 'boolean', 'default': False},
           'ignore_case': {'type': 'boolean', 'default': False},
           'limit': _LIMIT, 'offset': _OFFSET}, ('query',)),
    _tool('read_source', 'Read a source file',
          "Read numbered lines of one source file (py, xml, js, csv, po) of an installed module.",
          {'path': {'type': 'string', 'description': 'module/relative/path, for example sale/models/sale.py.'},
           'start_line': _START,
           'lines': {'type': 'integer', 'minimum': 1, 'maximum': MAX_READ_LINES, 'default': 200}}, ('path',)),
    _tool('list_modules', 'List modules',
          "Modules of this database with title, author, version and state; installed ones by default, and whether "
          "each is an application.",
          {'query': {'type': 'string', 'description': 'Filter on name, title or author.'},
           'state': {'type': 'string', 'enum': list(MODULE_STATES), 'default': 'installed'},
           'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}, 'offset': _OFFSET}),
    _tool('describe_module', 'Describe a module',
          "One installed module: manifest, dependencies, installed modules depending on it, models and wizards it "
          "defines or extends, and its views, menus, actions and reports.",
          {'module': {'type': 'string', 'description': 'Technical module name, for example sale.'}}, ('module',)),
    _tool('get_view', 'Get a view',
          "The view as this key's user sees it: arch after every inheriting view is applied, plus the inheriting "
          "views with their modules.",
          {'model': _MODEL, 'view_type': {'type': 'string', 'enum': list(VIEW_TYPES), 'default': 'form',
                                          'description': 'list is the same as tree on Odoo 10.'},
           'view': {'description': 'A view xmlid (sale.view_order_form) or id instead of the default view.'},
           'start_line': _START,
           'lines': {'type': 'integer', 'minimum': 1, 'maximum': MAX_READ_LINES, 'default': 300}}, ('model',)),
    _tool('list_actions', 'List actions and menus',
          "Window actions, server actions and reports of a model (or whose name matches query), with the menus "
          "that open them as this key's user sees them.",
          {'model': _MODEL, 'query': {'type': 'string', 'description': 'Filter on the action name.'},
           'limit': _LIMIT}),
    _tool('search_xmlids', 'Search external ids',
          "Find external ids (module.name) and the record each points to; the XML ids of one record with model "
          "and res_id.",
          {'query': {'type': 'string', 'description': 'Part of the id, or module.name_part.'},
           'model': dict(_MODEL, description='Only ids of records of this model.'),
           'res_id': {'type': 'integer', 'minimum': 1, 'description': 'With model: the ids of this one record.'},
           'limit': _LIMIT}),
)


def _text(arguments, name, shortest=1, required=True):
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, basestring) or len(value.strip()) < shortest:
        raise ToolArgumentError("%s must be a text of at least %s characters." % (name, shortest))
    return value.strip()


def _model(env, arguments):
    name = arguments.get('model')
    if not isinstance(name, basestring) or name not in env.registry:
        raise ToolArgumentError("Unknown model: %s. list_models with scope all lists every model." % (name,))
    return env[name]


def _page(rows, offset, limit, key):
    page = rows[offset:offset + limit]
    more = len(rows) > offset + limit
    return {key: page, 'returned': len(page), 'has_more': more, 'next_offset': offset + len(page) if more else None}


def _done(payload, model=None, count=0, ids=None):
    return payload, {'model': model, 'count': count, 'ids': ids or []}


def _compute_name(value):
    if not value:
        return None
    return value if isinstance(value, basestring) else getattr(value, '__name__', None)


def field_summary(model, field, home):
    entry = {'type': field.type, 'string': field.string}
    if field.comodel_name:
        entry['relation'] = field.comodel_name
    if field.type == 'one2many' and field.inverse_name:
        entry['inverse'] = field.inverse_name
    for name in ('required', 'readonly'):
        if getattr(field, name, False) is True:
            entry[name] = True
    if field.related:
        entry['related'] = '.'.join(field.related)
    elif field.compute:
        entry['compute'] = _compute_name(field.compute)
        if field.depends:
            entry['depends'] = list(field.depends)
    if not field.store:
        entry['stored'] = False
    if field.groups:
        entry['groups'] = field.groups
    if field.type in ('selection', 'reference') and isinstance(field.selection, (list, tuple)):
        entry['selection'] = [value for value, _label in field.selection][:40]
    module = getattr(field, '_module', None)
    if module and module != home:
        entry['module'] = module
    return entry


def _own_classes(model):
    return [cls for cls in type(model).__mro__ if explore.is_addon_class(cls) and explore.target_of(cls) == model._name]


def _describe_model(env, grant, arguments):
    model = _model(env, arguments)
    name = model._name
    home = type(model)._original_module
    own = _own_classes(model)
    loaded = []
    parents = []
    for cls in reversed(own):
        if cls._module not in loaded:
            loaded.append(cls._module)
        for parent in explore.parents_of(cls):
            if parent not in (name, 'base') and parent not in parents:
                parents.append(parent)
    asked = arguments.get('fields')
    if asked is not None and (not isinstance(asked, list) or not all(isinstance(item, basestring) for item in asked)):
        raise ToolArgumentError("fields must be a list of field names.")
    names = sorted(model._fields) if asked is None else [item for item in asked if item in model._fields]
    needle = (_text(arguments, 'query', required=False) or '').lower()
    if needle:
        names = [item for item in names if needle in item.lower()]
    fields = dict((field, field_summary(model, model._fields[field], home)) for field in names)
    mine = []
    inherited = {}
    for method, definitions in explore.index(env.registry).methods(name).items():
        if any(item.model == name for item in definitions):
            mine.append(method)
        else:
            origin = definitions[-1].model
            inherited[origin] = inherited.get(origin, 0) + 1
    payload = {
        'model': name, 'description': model._description, 'kind': explore.kind_of(model), 'module': home,
        'extended_by': [module for module in loaded if module != home], 'parents': parents,
        'delegates': dict(model._inherits), 'inherited_by': sorted(type(model)._inherit_children),
        'rec_name': model._rec_name, 'order': model._order, 'fields': fields,
        'methods': sorted(item for item in mine if needle in item.lower()),
        'inherited_methods': inherited, 'can': grant.rights(env, name, ACTIONS + ('call',)),
        'next': 'describe_method or get_method_source for a method; get_view and list_actions for its screens',
    }
    if asked is not None:
        payload['unknown_fields'] = [item for item in asked if item not in model._fields]
    if model._auto:
        payload['table'] = model._table
    return _done(payload, model=name, count=len(fields))


def _search_methods(env, grant, arguments):
    query = _text(arguments, 'query', 2).lower()
    limit = integer(arguments, 'limit', 50, 1, 200)
    offset = integer(arguments, 'offset', 0, 0, None)
    index = explore.index(env.registry)
    rows = []
    if arguments.get('model') is not None:
        model = _model(env, arguments)
        for method, definitions in sorted(index.methods(model._name).items()):
            if query in method.lower():
                row = {'method': method, 'modules': [item.module for item in definitions]}
                if definitions[-1].model != model._name:
                    row['from_model'] = definitions[-1].model
                rows.append(row)
        payload = _page(rows, offset, limit, 'methods')
        payload['model'] = model._name
        return _done(payload, model=model._name, count=payload['returned'])
    for method in sorted(index.own_methods()):
        if query in method.lower():
            for model_name, modules in index.own_methods()[method]:
                rows.append({'model': model_name, 'method': method, 'modules': modules})
    payload = _page(rows, offset, limit, 'methods')
    return _done(payload, count=payload['returned'])


def _definitions(env, model, arguments):
    method = _text(arguments, 'method')
    definitions = explore.index(env.registry).methods(model._name).get(method, [])
    if not definitions and not explore.is_framework_method(method):
        raise ToolArgumentError("%s has no method %s. search_methods finds methods by part of their name."
                                % (model._name, method))
    return method, definitions


def _framework_location(method):
    function = explore.unwrap(getattr(explore.models.BaseModel, method))
    function = getattr(function, 'im_func', function)
    try:
        return {'module': 'odoo', 'file': 'odoo/models.py', 'line': inspect.getsourcelines(function)[1]}
    except (IOError, TypeError):
        return {'module': 'odoo', 'file': 'odoo/models.py'}


def _raw_method(model, method, definitions):
    if definitions:
        return definitions[0].cls.__dict__[method]
    value = getattr(explore.models.BaseModel, method)
    return getattr(value, 'im_func', value)


def _signature(function):
    try:
        spec = inspect.getargspec(explore.unwrap(function))
    except TypeError:
        return None
    return inspect.formatargspec(*spec)


def _describe_method(env, grant, arguments):
    model = _model(env, arguments)
    method, definitions = _definitions(env, model, arguments)
    index = explore.index(env.registry)
    raw = _raw_method(model, method, definitions)
    payload = {'model': model._name, 'method': method, 'signature': method + (_signature(raw) or '(...)')}
    decorators = []
    api = getattr(raw, '_api', None)
    if api:
        decorators.append('api.%s' % api)
    for attribute, label in (('_depends', 'api.depends'), ('_onchange', 'api.onchange'),
                             ('_constrains', 'api.constrains')):
        value = getattr(raw, attribute, None)
        if value:
            decorators.append('%s(%s)' % (label, 'dynamic' if callable(value) else ', '.join(value)))
    returns = getattr(raw, '_returns', None)
    if returns:
        decorators.append('api.returns(%s)' % (returns[0],))
    payload['decorators'] = decorators
    documented = [inspect.getdoc(item.function) for item in definitions] + [inspect.getdoc(raw)]
    doc = next((text for text in documented if text), None)
    if doc:
        payload['doc'] = doc[:DOC_CHARS]
    roles = {}
    for field in model._fields.values():
        for role, value in (('computes', field.compute), ('inverses', field.inverse), ('searches', field.search)):
            if _compute_name(value) == method:
                roles.setdefault(role, []).append(field.name)
    roles.update((role, sorted(set(names))) for role, names in roles.items())
    payload.update(roles)
    overrides = []
    for item in definitions:
        path, start, lines = item.location()
        entry = {'module': item.module, 'file': index.relative(path), 'line': start, 'lines': len(lines),
                 'calls_super': any('super(' in line for line in lines)}
        if item.model != model._name:
            entry['model'] = item.model
        overrides.append(entry)
    payload['overrides'] = overrides
    if explore.is_framework_method(method):
        payload['framework'] = _framework_location(method)
    payload['callable_here'] = _callable(env, grant, model._name, method, definitions)
    payload['next'] = 'get_method_source for the code of every override'
    return _done(payload, model=model._name, count=len(overrides))


def _callable(env, grant, model_name, method, definitions):
    return bool(definitions and not method.startswith('_') and not explore.is_framework_method(method)
                and grant.permitted(env, model_name, 'call'))


def _get_method_source(env, grant, arguments):
    model = _model(env, arguments)
    method, definitions = _definitions(env, model, arguments)
    only = _text(arguments, 'module', required=False)
    if only:
        definitions = [item for item in definitions if item.module == only]
        if not definitions:
            raise ToolArgumentError("%s.%s has no definition in module %s. describe_method lists the modules."
                                    % (model._name, method, only))
    budget = integer(arguments, 'max_lines', 300, 1, MAX_SOURCE_LINES)
    index = explore.index(env.registry)
    overrides = []
    truncated = False
    for item in definitions:
        path, start, lines = item.location()
        relative = index.relative(path)
        entry = {'module': item.module, 'file': relative, 'line': start}
        if item.model != model._name:
            entry['model'] = item.model
        if relative is None:
            entry['source'] = None
        elif budget <= 0:
            entry['omitted'] = True
            truncated = True
        else:
            kept = lines[:budget]
            entry['source'] = ''.join(line[:MAX_LINE_CHARS * 2] for line in kept)
            if len(kept) < len(lines):
                entry['omitted_lines'] = len(lines) - len(kept)
                truncated = True
            budget -= len(kept)
        overrides.append(entry)
    payload = {'model': model._name, 'method': method, 'overrides': overrides, 'truncated': truncated}
    if explore.is_framework_method(method):
        payload['framework'] = _framework_location(method)
    if truncated:
        payload['next'] = 'read_source with file and line for the omitted part, or a larger max_lines'
    return _done(payload, model=model._name, count=len(overrides))


def _installed(env, names, argument):
    if not isinstance(names, list) or not all(isinstance(item, basestring) for item in names):
        raise ToolArgumentError("%s must be a list of module names." % argument)
    unknown = sorted(set(names) - env.registry._init_modules)
    if unknown:
        raise ToolArgumentError("Not installed here: %s. list_modules lists the installed modules." % ', '.join(unknown))
    return names


class GrepRun(object):
    """grep -E over module folders as a child process that is killed at a hard deadline; iterate it for (file, line, text)."""

    def __init__(self, query, ignore_case, roots, types):
        command = ['grep', '-rIn', '-Z', '--no-messages', '-E', '--exclude-dir=lib', '--exclude-dir=node_modules',
                   '--exclude-dir=.git']
        if ignore_case:
            command.append('-i')
        command += ['--include=*.%s' % kind for kind in types]
        command += ['-e', query, '--'] + sorted(roots)
        self.timed_out = False
        with open(os.devnull, 'w') as devnull:
            try:
                self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=devnull, close_fds=True)
            except OSError:
                raise ToolRefused("A regex search needs the grep program on the server; search without regex instead.")
        self.timer = threading.Timer(SEARCH_SECONDS, self._expire)
        self.timer.start()

    def _expire(self):
        self.timed_out = True
        try:
            self.process.kill()
        except OSError:
            pass

    def __iter__(self):
        for raw in iter(self.process.stdout.readline, b''):
            name, _nul, rest = raw.decode('utf-8', 'replace').rstrip(u'\n').partition(u'\0')
            number, _colon, text = rest.partition(u':')
            yield name, number, text

    def close(self):
        self.timer.cancel()
        if self.process.poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass
        self.process.stdout.close()
        self.process.wait()


def _check_pattern(query, ignore_case):
    """Refuse a regex grep cannot compile, with grep's own reason."""
    command = ['grep', '-E', '-e', query, os.devnull]
    if ignore_case:
        command.insert(2, '-i')
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
    except OSError:
        raise ToolRefused("A regex search needs the grep program on the server; search without regex instead.")
    _out, error = process.communicate()
    if process.returncode == 2:
        raise ToolArgumentError("Invalid regex: %s" % (error.decode('utf-8', 'replace').strip() or query)[:200])


def _shown(name, roots, known):
    """module/relative/path of a grep hit, or None when it is not a source file this tool may show."""
    if name in known:
        return known[name]
    shown = None
    for root in sorted(roots, key=len, reverse=True):
        if name.startswith(root + os.sep):
            parts = name[len(root) + 1:].split(os.sep)
            blocked = any(part.startswith('.') or part in explore.SKIPPED_DIRECTORIES for part in parts[:-1])
            real = os.path.realpath(name)
            inside = real.startswith(root + os.sep)
            if inside and not blocked and not parts[-1].endswith('.min.js') \
                    and os.path.getsize(real) <= explore.MAX_FILE_BYTES:
                shown = roots[root] + '/' + '/'.join(parts)
            break
    known[name] = shown
    return shown


def _search_code(env, grant, arguments):
    query = _text(arguments, 'query', 2)
    regex = flag(arguments, 'regex')
    ignore_case = flag(arguments, 'ignore_case')
    index = explore.index(env.registry)
    modules = index.modules()
    if arguments.get('module') is not None:
        modules = _installed(env, [_text(arguments, 'module')], 'module')
    types = arguments.get('file_types', list(DEFAULT_FILE_TYPES))
    if not isinstance(types, list) or not types or not set(types) <= set(explore.SOURCE_TYPES):
        raise ToolArgumentError("file_types must be a list of: %s." % ', '.join(explore.SOURCE_TYPES))
    pattern = _text(arguments, 'path', required=False)
    limit = integer(arguments, 'limit', 50, 1, 200)
    offset = integer(arguments, 'offset', 0, 0, None)
    skip = offset
    matches = []
    more = False
    timed_out = False
    scanned = None
    if regex:
        if len(query) > MAX_PATTERN or '\n' in query or '\x00' in query:
            raise ToolArgumentError("A regex must be one line of at most %s characters." % MAX_PATTERN)
        if re.search(r'\\[1-9]', query):
            raise ToolArgumentError("Backreferences are not supported in a regex search.")
        _check_pattern(query, ignore_case)
        roots = dict((index.module_root(module), module) for module in modules if index.module_root(module))
        known = {}
        run = GrepRun(query, ignore_case, roots, types) if roots else None
        try:
            for name, number, text in run or ():
                shown = _shown(name, roots, known)
                if shown is None or (pattern and not fnmatch.fnmatchcase(shown, pattern)):
                    continue
                if skip:
                    skip -= 1
                elif len(matches) == limit:
                    more = True
                    break
                else:
                    matches.append({'file': shown, 'line': int(number), 'text': text.strip()[:MATCH_CHARS]})
        finally:
            if run:
                run.close()
                timed_out = run.timed_out
    else:
        needle = query.lower() if ignore_case else query
        found_in = lambda line: needle in (line.lower() if ignore_case else line)
        scanned = 0
        for module in modules:
            for relative, extension, real in index.files(module):
                if extension not in types or (pattern and not fnmatch.fnmatchcase(relative, pattern)):
                    continue
                text = explore.read_text(real)
                if text is None:
                    continue
                scanned += 1
                for number, line in enumerate(text.splitlines(), 1):
                    if not found_in(line):
                        continue
                    if skip:
                        skip -= 1
                    elif len(matches) == limit:
                        more = True
                        break
                    else:
                        matches.append({'file': relative, 'line': number, 'text': line.strip()[:MATCH_CHARS]})
                if more:
                    break
            if more:
                break
    payload = {'matches': matches, 'returned': len(matches), 'has_more': more,
               'next_offset': offset + len(matches) if more else None}
    if scanned is not None:
        payload['files_scanned'] = scanned
    if timed_out:
        payload['timed_out'] = True
        payload['next'] = 'the search stopped after %s seconds; narrow it with module, file_types or path' % SEARCH_SECONDS
    return _done(payload, model=pattern, count=len(matches))


def _read_source(env, grant, arguments):
    relative = _text(arguments, 'path')
    real = explore.index(env.registry).resolve(relative)
    if real is None:
        raise ToolArgumentError("Not a source file (%s) of an installed module: %s. Paths look like "
                                "sale/models/sale.py." % (', '.join(explore.SOURCE_TYPES), relative))
    text = explore.read_text(real)
    if text is None:
        raise ToolArgumentError("%s cannot be read: it is larger than %s MB or not a plain file."
                                % (relative, explore.MAX_FILE_BYTES // (1024 * 1024)))
    start = integer(arguments, 'start_line', 1, 1, None)
    count = integer(arguments, 'lines', 200, 1, MAX_READ_LINES)
    lines = text.splitlines()
    chosen = lines[start - 1:start - 1 + count]
    numbered = '\n'.join('%d: %s' % (start + offset, line[:MAX_LINE_CHARS]) for offset, line in enumerate(chosen))
    end = start + len(chosen) - 1
    payload = {'path': relative, 'start_line': start, 'end_line': end, 'total_lines': len(lines), 'text': numbered}
    if end < len(lines):
        payload['next_start_line'] = end + 1
    return _done(payload, model=relative, count=len(chosen))


def _module_rows(env, state='installed'):
    if state == 'installed':
        domain = [('name', 'in', sorted(env.registry._init_modules)), ('state', 'in', ('installed', 'to upgrade', 'to remove'))]
    elif state == 'all':
        domain = []
    else:
        domain = [('state', '=', state)]
    return env['ir.module.module'].sudo().search_read(
        domain, ['name', 'shortdesc', 'author', 'application', 'latest_version', 'summary', 'state'], order='name')


def _list_modules(env, grant, arguments):
    query = (_text(arguments, 'query', required=False) or '').lower()
    state = arguments.get('state', 'installed')
    if state not in MODULE_STATES:
        raise ToolArgumentError("state must be one of: %s." % ', '.join(MODULE_STATES))
    limit = integer(arguments, 'limit', 100, 1, 500)
    offset = integer(arguments, 'offset', 0, 0, None)
    found = []
    for row in _module_rows(env, state):
        entry = {'name': row['name'], 'title': row['shortdesc'], 'author': row['author'] or ''}
        if state != 'installed':
            entry['state'] = row['state']
        if row['application']:
            entry['application'] = True
        if query and not any(query in (value or '').lower() for value in entry.values() if value is not True):
            continue
        found.append(entry)
    payload = _page(found, offset, limit, 'modules')
    payload['installed'] = len(env.registry._init_modules)
    return _done(payload, count=payload['returned'])


def _describe_module(env, grant, arguments):
    name = _text(arguments, 'module')
    _installed(env, [name], 'module')
    manifest = odoo_module.load_information_from_description_file(name) or {}
    record = env['ir.module.module'].sudo().search([('name', '=', name)], limit=1)
    env.cr.execute("""
        SELECT DISTINCT m.name FROM ir_module_module_dependency d JOIN ir_module_module m ON m.id = d.module_id
         WHERE d.name = %s AND m.state IN ('installed', 'to upgrade') ORDER BY m.name""", (name,))
    dependents = [row[0] for row in env.cr.fetchall()]
    defined, extended = explore.index(env.registry).models_of(name)
    env.cr.execute("SELECT model, count(*) FROM ir_model_data WHERE module = %s GROUP BY model ORDER BY model", (name,))
    records = dict(env.cr.fetchall())
    payload = {
        'name': name, 'title': record.shortdesc, 'summary': record.summary or manifest.get('summary') or '',
        'version': record.latest_version, 'author': record.author or '', 'category': record.category_id.name or '',
        'application': bool(record.application), 'depends': list(manifest.get('depends') or []),
        'dependents': dependents, 'data_files': list(manifest.get('data') or []),
        'models_defined': [model for model in defined if not env[model]._transient],
        'wizards_defined': [model for model in defined if env[model]._transient],
        'models_extended': extended, 'records': records,
    }
    for label, model in (('views', 'ir.ui.view'), ('actions', 'ir.actions.act_window'),
                         ('server_actions', 'ir.actions.server'), ('reports', 'ir.actions.report.xml')):
        if records.get(model):
            payload[label] = _xmlids(env, name, model, 30)
    menus = _xmlids(env, name, 'ir.ui.menu', 60, with_ids=True)
    if menus:
        found = env['ir.ui.menu'].sudo().browse([res_id for _xmlid, res_id in menus]).exists()
        paths = dict((menu.id, menu.complete_name) for menu in found)
        payload['menus'] = [paths[res_id] for _xmlid, res_id in menus if res_id in paths][:30]
    payload['next'] = 'describe_model for a model, search_code with modules [%s] for its code' % name
    return _done(payload, model=name, count=len(defined) + len(extended))


def _xmlids(env, module, model, limit, with_ids=False):
    env.cr.execute("SELECT name, res_id FROM ir_model_data WHERE module = %s AND model = %s ORDER BY name LIMIT %s",
                   (module, model, limit))
    rows = env.cr.fetchall()
    if with_ids:
        return [('%s.%s' % (module, xmlid), res_id) for xmlid, res_id in rows]
    return ['%s.%s' % (module, xmlid) for xmlid, _res_id in rows]


def _record_xmlids(env, model, ids):
    if not ids:
        return {}
    env.cr.execute("SELECT res_id, module, name FROM ir_model_data WHERE model = %s AND res_id IN %s ORDER BY id",
                   (model, tuple(ids)))
    found = {}
    for res_id, module, name in env.cr.fetchall():
        found.setdefault(res_id, '%s.%s' % (module, name))
    return found


def _view_id(env, xmlid):
    model, res_id = env['ir.model.data'].sudo().xmlid_to_res_model_res_id(xmlid)
    if model != 'ir.ui.view' or not res_id:
        raise ToolArgumentError("%s is not the XML id of a view." % xmlid)
    return res_id


def _pretty(arch):
    parser = etree.XMLParser(remove_blank_text=True)
    node = etree.fromstring(arch.encode('utf-8') if isinstance(arch, unicode) else arch, parser)
    return etree.tostring(node, pretty_print=True, encoding='unicode')


def _get_view(env, grant, arguments):
    model = _model(env, arguments)
    view_type = arguments.get('view_type', 'form')
    if view_type not in VIEW_TYPES:
        raise ToolArgumentError("view_type must be one of: %s." % ', '.join(VIEW_TYPES))
    if view_type == 'list':
        view_type = 'tree'
    requested = arguments.get('view')
    view_id = None
    if isinstance(requested, basestring) and requested.strip():
        view_id = _view_id(env, requested.strip())
    elif isinstance(requested, (int, long)) and not isinstance(requested, bool):
        if not env['ir.ui.view'].sudo().browse(requested).exists():
            raise ToolArgumentError("No view with id %s." % requested)
        view_id = requested
    elif requested is not None:
        raise ToolArgumentError("view must be a view xmlid or id.")
    start = integer(arguments, 'start_line', 1, 1, None)
    count = integer(arguments, 'lines', 300, 1, MAX_READ_LINES)
    described = model.fields_view_get(view_id=view_id, view_type=view_type)
    lines = _pretty(described['arch']).splitlines()
    chosen = lines[start - 1:start - 1 + count]
    root = described.get('view_id')
    views = env['ir.ui.view'].sudo()
    chain = []
    frontier = [root] if root else []
    while frontier and len(chain) < 200:
        children = views.search([('inherit_id', 'in', frontier)], order='priority, id')
        chain.extend(children)
        frontier = children.ids
    names = _record_xmlids(env, 'ir.ui.view', [root] + [item.id for item in chain] if root else [])
    end = start + len(chosen) - 1
    payload = {
        'model': model._name, 'view_type': view_type, 'view_id': root, 'xmlid': names.get(root),
        'inherited_by': [{'xmlid': names.get(item.id), 'name': item.name, 'inherits': names.get(item.inherit_id.id)}
                         for item in chain],
        'start_line': start, 'end_line': end, 'total_lines': len(lines), 'arch': '\n'.join(chosen),
    }
    if end < len(lines):
        payload['next_start_line'] = end + 1
    return _done(payload, model=model._name, count=len(chosen), ids=[root] if root else [])


def _list_actions(env, grant, arguments):
    name = arguments.get('model')
    query = _text(arguments, 'query', required=False)
    if name is None and not query:
        raise ToolArgumentError("Give model, query or both.")
    if name is not None:
        name = _model(env, arguments)._name
    limit = integer(arguments, 'limit', 50, 1, 200)

    def domain(model_field):
        terms = []
        if name:
            terms.append((model_field, '=', name))
        if query:
            terms.append(('name', 'ilike', query))
        return terms

    windows = env['ir.actions.act_window'].search(domain('res_model'), limit=limit, order='id')
    references = ['ir.actions.act_window,%d' % action.id for action in windows]
    menus = env['ir.ui.menu'].search([('action', 'in', references)]) if references else env['ir.ui.menu']
    opened = {}
    for menu in menus:
        opened.setdefault(menu.action.id, []).append(menu.complete_name)
    window_ids = _record_xmlids(env, 'ir.actions.act_window', windows.ids)
    payload = {'window_actions': [{
        'id': action.id, 'xmlid': window_ids.get(action.id), 'name': action.name, 'model': action.res_model,
        'view_mode': action.view_mode, 'domain': action.domain or None, 'context': action.context or None,
        'menus': opened.get(action.id, [])} for action in windows]}
    servers = env['ir.actions.server'].search(domain('model_id.model'), limit=limit, order='id')
    server_ids = _record_xmlids(env, 'ir.actions.server', servers.ids)
    payload['server_actions'] = [{'id': action.id, 'xmlid': server_ids.get(action.id), 'name': action.name,
                                  'model': action.model_id.model, 'state': action.state} for action in servers]
    reports = env['ir.actions.report.xml'].search(domain('model'), limit=limit, order='id')
    report_ids = _record_xmlids(env, 'ir.actions.report.xml', reports.ids)
    payload['reports'] = [{'id': report.id, 'xmlid': report_ids.get(report.id), 'name': report.name,
                           'model': report.model, 'report_name': report.report_name,
                           'report_type': report.report_type} for report in reports]
    count = len(windows) + len(servers) + len(reports)
    return _done(payload, model=name, count=count)


def _search_xmlids(env, grant, arguments):
    limit = integer(arguments, 'limit', 50, 1, 200)
    data = env['ir.model.data'].sudo()
    query = _text(arguments, 'query', 2, required=False)
    if arguments.get('res_id') is not None:
        if arguments.get('model') is None:
            raise ToolArgumentError("res_id needs model.")
        model = _model(env, arguments)
        res_id = integer(arguments, 'res_id', 1, 1, None)
        found = data.search([('model', '=', model._name), ('res_id', '=', res_id)], limit=limit)
        rows = [{'xmlid': '%s.%s' % (item.module, item.name), 'model': model._name, 'res_id': res_id,
                 'noupdate': item.noupdate} for item in found]
        return _done({'xmlids': rows, 'returned': len(rows), 'has_more': False}, model=model._name,
                     count=len(rows), ids=[res_id])
    if not query:
        raise ToolArgumentError("Give query, or model with res_id.")
    module, dot, short = query.partition('.')
    if dot:
        domain = [('module', '=', module), ('name', 'ilike', short)]
    else:
        domain = ['|', ('name', 'ilike', query), ('module', 'ilike', query)]
    only = None
    if arguments.get('model') is not None:
        only = _model(env, arguments)._name
        domain.append(('model', '=', only))
    found = data.search(domain, limit=limit + 1, order='module, name')
    rows = []
    for item in found[:limit]:
        row = {'xmlid': '%s.%s' % (item.module, item.name), 'model': item.model, 'res_id': item.res_id}
        if len(found) <= 10 and item.model in env.registry:
            record = env[item.model].sudo().browse(item.res_id).exists()
            row['exists'] = bool(record)
            if record and grant.permitted(env, item.model, 'read'):
                row['display_name'] = env[item.model].browse(item.res_id).display_name
        rows.append(row)
    payload = {'xmlids': rows, 'returned': len(rows), 'has_more': len(found) > limit}
    return _done(payload, model=only, count=len(rows))


OVERVIEW = {'uri': 'odoo://overview', 'name': 'overview', 'title': 'Database overview', 'mimeType': 'text/markdown',
            'description': 'Odoo version, installed applications, modules not written by Odoo, and model counts.'}
ODOO_AUTHORS = ('odoo s.a.', 'odoo sa', 'openerp sa', 'openerp s.a.')


def overview(env):
    """Markdown map of this database for an agent that has just connected."""
    rows = _module_rows(env)
    others = [row for row in rows if (row['author'] or '').strip().lower() not in ODOO_AUTHORS]
    applications = [row for row in rows if row['application']]
    names = env.registry.models
    wizards = sum(1 for name in names if env[name]._transient)
    abstract = sum(1 for name in names if env[name]._abstract)
    lines = [
        '# Odoo %s, database %s' % (odoo.release.version, env.cr.dbname),
        '',
        'Installed modules: %d (%d by Odoo S.A., %d by other authors).' % (len(rows), len(rows) - len(others), len(others)),
        'Models: %d (%d wizards, %d abstract).' % (len(names), wizards, abstract),
        '',
        '## Applications',
    ]
    lines.extend('- %s: %s' % (row['name'], row['shortdesc']) for row in applications)
    lines.extend(['', '## Modules not by Odoo S.A.'])
    lines.extend('- %s: %s (%s)' % (row['name'], row['shortdesc'], row['author'] or 'no author') for row in others[:150])
    if len(others) > 150:
        lines.append('- ... %d more: list_modules' % (len(others) - 150))
    lines.extend(['', '## Explore', 'describe_module on a custom module, then describe_model on its models, '
                  'describe_method and get_method_source for the logic, get_view for the screens.'])
    return '\n'.join(lines)


HANDLERS = dict((tool['name'], globals()['_' + tool['name']]) for tool in DEFINITIONS)
