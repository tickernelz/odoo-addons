"""Read-only introspection of one registry: models, methods, source files, modules, views and actions."""
import inspect
import os
import re
import subprocess
import threading
import weakref

import odoo
import odoo.orm
from odoo.modules.module import get_manifest, get_module_path

from .grants import ToolArgumentError

SOURCE_TYPES = ('py', 'xml', 'js', 'csv', 'po', 'pot')
SEARCH_TYPES = ('py', 'xml', 'js', 'csv')
CORE_PREFIXES = ('odoo.orm.', 'odoo.models')
ADDON_PREFIX = 'odoo.addons.'
MODULE_NAME = re.compile(r'^[A-Za-z0-9_]+$')
CORE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(odoo.orm.__file__))))
SEARCH_SECONDS = 10
LINE_CHARS = 240
CACHED_MODELS = 64
ODOO_AUTHOR = 'Odoo S.A.'

_LOCK = threading.Lock()
_INDEXES = {}


class Definition(object):
    """One definition of a method in the class hierarchy of a model."""

    __slots__ = ('owner', 'module', 'path', 'line', 'super_call')

    def __init__(self, owner, module, path, line, super_call):
        self.owner = owner
        self.module = module
        self.path = path
        self.line = line
        self.super_call = super_call

    def as_dict(self):
        found = {'module': self.module, 'path': '%s:%s' % (self.path, self.line)}
        if self.owner:
            found['model'] = self.owner
        if self.super_call:
            found['calls_super'] = True
        return found


def index(env):
    """The introspection index of env's registry, rebuilt whenever Odoo replaces the registry."""
    registry = env.registry
    with _LOCK:
        current = _INDEXES.get(registry.db_name)
        if current is None or current.registry() is not registry:
            current = Index(registry)
            _INDEXES[registry.db_name] = current
        return current


def function_of(value):
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    if not inspect.isfunction(value):
        return None
    return inspect.unwrap(value)


def is_registry_class(klass):
    return 'pool' in klass.__dict__


def addon_of(klass):
    name = klass.__module__ or ''
    if name.startswith(ADDON_PREFIX):
        return name.split('.')[2]
    return None


def is_core_class(klass):
    return (klass.__module__ or '').startswith(CORE_PREFIXES) and not is_registry_class(klass)


class Index(object):
    """Plain data derived from one registry; holds no reference that keeps the registry alive."""

    def __init__(self, registry):
        self.registry = weakref.ref(registry)
        self._lock = threading.Lock()
        self._chains = {}
        self._own = None
        self._roots = {}
        self._paths = {}
        self._orm = None

    def module_root(self, module):
        root = self._roots.get(module)
        if root is None:
            path = get_module_path(module, display_warning=False)
            root = os.path.realpath(path) if path else ''
            self._roots[module] = root
        return root

    def relative_path(self, filename, module):
        key = (filename, module)
        found = self._paths.get(key)
        if found is None:
            real = os.path.realpath(filename)
            root = self.module_root(module) if module else ''
            if root and real.startswith(root + os.sep):
                found = module + '/' + os.path.relpath(real, root)
            elif real.startswith(CORE_ROOT + os.sep):
                found = os.path.relpath(real, CORE_ROOT)
            else:
                found = os.path.basename(real)
            self._paths[key] = found
        return found

    def _definition(self, func, owner, module):
        code = func.__code__
        return Definition(owner, module or 'odoo', self.relative_path(code.co_filename, module),
                          code.co_firstlineno, 'super' in code.co_names)

    def chains(self, model_class):
        """{method: [Definition, ...]} for every method of the model, most derived first."""
        name = model_class._name
        with self._lock:
            found = self._chains.get(name)
        if found is not None:
            return found
        found = {}
        owner = None
        for klass in model_class.__mro__:
            if is_registry_class(klass):
                owner = klass._name
                continue
            module = addon_of(klass)
            if module is None and not is_core_class(klass):
                continue
            for attribute, value in vars(klass).items():
                func = function_of(value)
                if func is None:
                    continue
                found.setdefault(attribute, []).append(
                    self._definition(func, owner if module else None, module))
        with self._lock:
            if len(self._chains) >= CACHED_MODELS:
                self._chains.pop(next(iter(self._chains)))
            self._chains[name] = found
        return found

    def orm_names(self, registry):
        """Method names every model inherits from the ORM itself or from the 'base' model."""
        with self._lock:
            if self._orm is not None:
                return self._orm
        names = set()
        for klass in registry['base'].__mro__:
            if is_registry_class(klass) or addon_of(klass) or is_core_class(klass):
                names.update(attribute for attribute, value in vars(klass).items() if function_of(value))
        with self._lock:
            self._orm = frozenset(names)
        return self._orm

    def own_methods(self, registry):
        """[(lowercase name, method, model, module)] for methods each model defines itself."""
        with self._lock:
            if self._own is not None:
                return self._own
        own = []
        for model_name in sorted(registry):
            for klass in registry[model_name].__bases__:
                module = addon_of(klass)
                if module is None or is_registry_class(klass):
                    continue
                for attribute, value in vars(klass).items():
                    if function_of(value):
                        own.append((attribute.lower(), attribute, model_name, module))
        with self._lock:
            self._own = own
        return own

    def function(self, model_class, method, definition):
        """The function object behind one Definition, looked up again in the live class."""
        for klass in model_class.__mro__:
            if is_registry_class(klass):
                continue
            func = function_of(vars(klass).get(method))
            if func is None:
                continue
            module = addon_of(klass) or 'odoo'
            code = func.__code__
            if module == definition.module and code.co_firstlineno == definition.line:
                return func
        return None


def model_class(env, model_name):
    if not isinstance(model_name, str) or model_name not in env.registry:
        raise ToolArgumentError("Unknown model: %s" % (model_name,))
    return type(env[model_name])


def kind_of(model):
    if model._abstract:
        return 'abstract'
    if model._transient:
        return 'transient'
    return 'model'


def decorators(func):
    found = {}
    for attribute, label in (('_depends', 'depends'), ('_onchange', 'onchange'), ('_constrains', 'constrains'),
                             ('_depends_context', 'depends_context')):
        value = getattr(func, attribute, None)
        if value:
            found[label] = list(value) if isinstance(value, (list, tuple)) else '%s' % (value,)
    for attribute, label in (('_api_model', 'model'), ('_api_private', 'private'), ('_readonly', 'readonly'),
                             ('_autovacuum', 'autovacuum')):
        if getattr(func, attribute, False):
            found[label] = True
    if getattr(func, '_ondelete', None) is not None:
        found['ondelete'] = True
    return found


def signature(func):
    try:
        return '%s%s' % (func.__name__, inspect.signature(func))
    except (TypeError, ValueError):
        return func.__name__


def first_doc_line(func):
    doc = inspect.getdoc(func) or ''
    return doc.strip().split('\n\n')[0].replace('\n', ' ')[:400]


def field_line(env, model, field):
    """One line describing a field: type, label, flags, compute chain and defining modules."""
    parts = [field.type]
    if getattr(field, 'comodel_name', None):
        parts[0] = '%s(%s)' % (field.type, field.comodel_name)
    label = field._description_string(env)
    if label != default_label(field.name):
        parts.append(repr(label))
    if field.required:
        parts.append('required')
    if field.compute or field.related:
        if field.store:
            parts.append('stored')
    elif not field.store:
        parts.append('not stored')
    if field.readonly and not field.compute and not field.related:
        parts.append('readonly')
    if field.related:
        parts.append('related=%s' % (field.related if isinstance(field.related, str) else '.'.join(field.related)))
    elif field.compute:
        compute = field.compute if isinstance(field.compute, str) else getattr(field.compute, '__name__', 'lambda')
        try:
            depends = list(field.get_depends(model)[0])
        except Exception:
            depends = []
        parts.append('compute=%s%s' % (compute, '(%s)' % ', '.join(depends) if depends else ''))
        if field.inverse:
            parts.append('inverse=%s' % (field.inverse if isinstance(field.inverse, str) else 'lambda'))
    if field.type == 'one2many' and field.inverse_name:
        parts.append('inverse_name=%s' % field.inverse_name)
    if field.type in ('selection', 'reference') and isinstance(field.selection, (list, tuple)):
        parts.append('selection=[%s]' % ', '.join('%s' % (key,) for key, _label in field.selection))
    if field.groups:
        parts.append('groups=%s' % field.groups)
    if field.company_dependent:
        parts.append('company_dependent')
    modules = list(field._modules or ())
    if len(modules) > 1:
        parts.append('extended_by=%s' % ','.join(modules[1:]))
    return ' '.join(parts)


def default_label(name):
    base = name[:-4] if name.endswith('_ids') else name[:-3] if name.endswith('_id') else name
    return base.replace('_', ' ').title()


def describe_model(env, grant, model_name, only_fields=None, query=None):
    klass = model_class(env, model_name)
    model = env[model_name]
    idx = index(env)
    chains = idx.chains(klass)
    own_modules = []
    for base in klass.__bases__:
        module = addon_of(base)
        if module and not is_registry_class(base) and module not in own_modules:
            own_modules.append(module)
    defined_in = klass._original_module
    parents = [base._name for base in klass.__bases__ if is_registry_class(base)]
    needle = (query or '').lower()
    fields = {}
    inherited_fields = {}
    from_parent = {}
    for parent in parents:
        if parent != 'base' and parent not in model._inherits:
            for name in env.registry[parent]._fields:
                from_parent.setdefault(name, parent)
    names = sorted(model._fields)
    if only_fields is not None:
        unknown = [name for name in only_fields if name not in model._fields]
        if unknown:
            raise ToolArgumentError("Unknown fields on %s: %s" % (model_name, ', '.join(unknown)))
        names = list(only_fields)
    for name in names:
        if needle and needle not in name.lower():
            continue
        field = model._fields[name]
        parent = from_parent.get(name)
        if only_fields is None and parent and set(field._modules or ()) <= set(env.registry[parent]._fields[name]._modules or ()):
            inherited_fields.setdefault(parent, []).append(name)
            continue
        definer = (field._modules or (defined_in,))[0]
        fields.setdefault(definer, {})[name] = field_line(env, model, field)
    public = []
    orm_overrides = []
    inherited = {}
    orm = idx.orm_names(env.registry)
    for name in sorted(chains):
        if name.startswith('_') or (needle and needle not in name.lower()):
            continue
        owners = [item.owner for item in chains[name]]
        if model_name in owners:
            (orm_overrides if name in orm else public).append(name)
        elif owners[-1] not in ('base', None):
            inherited[owners[-1]] = inherited.get(owners[-1], 0) + 1
    private = [name for name in chains if name.startswith('_')
               and any(item.owner == model_name for item in chains[name])]
    user_access = dict((mode, model.has_access(mode)) for mode in ('read', 'create', 'write', 'unlink'))
    result = {
        'model': model_name,
        'description': model._description,
        'kind': kind_of(model),
        'table': model._table if not model._abstract else None,
        'rec_name': model._rec_name,
        'order': model._order,
        'defined_in': defined_in,
        'extended_by': [module for module in reversed(own_modules) if module != defined_in],
        'inherits': parents,
        'delegates_to': dict(model._inherits),
        'inherited_by': sorted(klass._inherit_children)[:30],
        'access': {'user': user_access, 'key': grant.model_access(env, model_name)},
        'fields': fields,
        'fields_from_parents': inherited_fields,
        'methods': public,
        'orm_overrides': orm_overrides,
        'inherited_public_methods': inherited,
        'private_methods_defined_here': len(private),
        'next': ['describe_method(model, method)', 'get_method_source(model, method)',
                 'search_methods(query, model) for private ones such as compute methods'],
    }
    if only_fields is None and not needle:
        result['field_count'] = len(fields)
    return result


def search_methods(env, query, model_name=None, limit=50, offset=0):
    if not isinstance(query, str) or not query.strip():
        raise ToolArgumentError("query must name part of a method, for example confirm.")
    terms = query.lower().split()
    idx = index(env)
    if model_name is not None:
        klass = model_class(env, model_name)
        chains = idx.chains(klass)
        rows = []
        for name in sorted(chains):
            if all(term in name.lower() for term in terms):
                modules = []
                for item in chains[name]:
                    if item.module not in modules:
                        modules.append(item.module)
                rows.append({'model': model_name, 'method': name, 'modules': modules})
    else:
        grouped = {}
        for lowered, name, owner, module in idx.own_methods(env.registry):
            if all(term in lowered for term in terms):
                modules = grouped.setdefault((owner, name), [])
                if module not in modules:
                    modules.append(module)
        rows = [{'model': owner, 'method': name, 'modules': list(reversed(modules))}
                for (owner, name), modules in sorted(grouped.items(), key=lambda item: (
                    item[0][1] != query, item[0][1].startswith('_'), item[0][0], item[0][1]))]
    page = rows[offset:offset + limit]
    return {'methods': page, 'returned': len(page), 'has_more': len(rows) > offset + limit,
            'next_offset': offset + limit if len(rows) > offset + limit else None,
            'next': ['describe_method(model, method)', 'get_method_source(model, method)']}


def describe_method(env, model_name, method):
    klass = model_class(env, model_name)
    idx = index(env)
    chain = idx.chains(klass).get(method) if isinstance(method, str) else None
    if not chain:
        raise ToolArgumentError("Unknown method %s on %s." % (method, model_name))
    top = idx.function(klass, method, chain[0])
    doc = ''
    for item in chain:
        func = idx.function(klass, method, item)
        if func is not None and inspect.getdoc(func):
            doc = first_doc_line(func)
            break
    model = env[model_name]
    used_by = []
    for name, field in model._fields.items():
        for role in ('compute', 'inverse', 'search'):
            if getattr(field, role, None) == method:
                used_by.append('%s (%s)' % (name, role))
    callable_here, reason = callable_by_tool(env, model_name, method)
    result = {
        'model': model_name,
        'method': method,
        'signature': signature(top) if top else method,
        'decorators': decorators(top) if top else {},
        'doc': doc,
        'overrides': [item.as_dict() for item in chain],
        'used_by_fields': used_by,
        'call_method': 'allowed' if callable_here else 'refused: %s' % reason,
        'next': ['get_method_source(model, method)', 'read_source(path) for the code around a definition'],
    }
    return result


def method_source(env, model_name, method, module=None, max_lines=300):
    klass = model_class(env, model_name)
    idx = index(env)
    chain = idx.chains(klass).get(method) if isinstance(method, str) else None
    if not chain:
        raise ToolArgumentError("Unknown method %s on %s." % (method, model_name))
    if module is not None:
        chain = [item for item in chain if item.module == module]
        if not chain:
            raise ToolArgumentError("Module %s does not define %s.%s." % (module, model_name, method))
    sources = []
    budget = max_lines
    truncated = False
    for item in chain:
        entry = item.as_dict()
        if item.module == 'odoo':
            entry['source'] = None
            entry['note'] = 'Odoo core (outside the addons folders); not readable here.'
            sources.append(entry)
            continue
        func = idx.function(klass, method, item)
        try:
            lines, start = inspect.getsourcelines(func)
        except (TypeError, OSError):
            entry['source'] = None
            sources.append(entry)
            continue
        if budget <= 0:
            truncated = True
            entry['source'] = None
            sources.append(entry)
            continue
        kept = lines[:budget]
        budget -= len(kept)
        if len(kept) < len(lines):
            truncated = True
        entry['lines'] = '%s-%s' % (start, start + len(lines) - 1)
        entry['source'] = ''.join(kept)
        sources.append(entry)
    result = {'model': model_name, 'method': method, 'definitions': sources}
    if truncated:
        result['truncated'] = True
        result['next'] = ['get_method_source(model, method, module) for one definition',
                          'read_source(path, start_line) for the rest']
    return result


def callable_by_tool(env, model_name, method):
    """(True, None) when call_method may run method on model_name, else (False, reason)."""
    if method.startswith('_'):
        return False, 'private method'
    klass = type(env[model_name])
    func = getattr(klass, method, None)
    if not callable(func):
        return False, 'not a method of this model'
    for item in klass.__mro__:
        if getattr(vars(item).get(method), '_api_private', False):
            return False, 'marked @api.private'
    if method in index(env).orm_names(env.registry):
        return False, 'ORM method, use the record tools'
    return True, None


def installed_modules(env):
    env.cr.execute("SELECT name FROM ir_module_module WHERE state = 'installed' ORDER BY name")
    return [row[0] for row in env.cr.fetchall()]


def resolve_source(env, path):
    """Absolute file for 'module/relative/path.ext' inside a module folder of the addons path."""
    if not isinstance(path, str) or not path or '\x00' in path or path.startswith('/'):
        raise ToolArgumentError("path must look like module/relative/file.py.")
    module, _sep, rest = path.partition('/')
    if not MODULE_NAME.match(module) or not rest:
        raise ToolArgumentError("path must look like module/relative/file.py.")
    if rest.rsplit('.', 1)[-1] not in SOURCE_TYPES or '.' not in rest:
        raise ToolArgumentError("Only %s files can be read." % ', '.join(SOURCE_TYPES))
    root = index(env).module_root(module)
    if not root:
        raise ToolArgumentError("Unknown module: %s" % module)
    target = os.path.realpath(os.path.join(root, rest))
    if not target.startswith(root + os.sep) or not os.path.isfile(target):
        raise ToolArgumentError("No such file: %s" % path)
    return target


def read_source(env, path, start_line=1, lines=200):
    target = resolve_source(env, path)
    with open(target, 'r', encoding='utf-8', errors='replace') as handle:
        text = handle.read().splitlines()
    total = len(text)
    start = max(1, start_line)
    window = text[start - 1:start - 1 + lines]
    end = start + len(window) - 1
    numbered = '\n'.join('%s: %s' % (start + offset, line) for offset, line in enumerate(window))
    result = {'path': path, 'start_line': start, 'end_line': end, 'total_lines': total, 'text': numbered}
    if end < total:
        result['next'] = 'read_source(path, start_line=%s)' % (end + 1)
    return result


def search_code(env, query, module=None, file_types=None, regex=False, ignore_case=False, limit=50):
    if not isinstance(query, str) or not query or len(query) > 200 or '\n' in query:
        raise ToolArgumentError("query must be one line of text, at most 200 characters.")
    types = file_types or list(SEARCH_TYPES)
    if not isinstance(types, list) or any(kind not in SOURCE_TYPES for kind in types):
        raise ToolArgumentError("file_types may only hold %s." % ', '.join(SOURCE_TYPES))
    idx = index(env)
    if module is not None:
        if not isinstance(module, str) or not MODULE_NAME.match(module) or not idx.module_root(module):
            raise ToolArgumentError("Unknown module: %s" % (module,))
        modules = [module]
    else:
        modules = installed_modules(env)
    roots = {}
    for name in modules:
        root = idx.module_root(name)
        if root:
            roots[root] = name
    if not roots:
        return {'matches': [], 'returned': 0, 'has_more': False}
    command = ['grep', '-rIn', '--no-messages', '-E' if regex else '-F', '--exclude-dir=lib', '--exclude-dir=node_modules']
    if ignore_case:
        command.append('-i')
    command += ['--include=*.%s' % kind for kind in types]
    command += ['-e', query, '--'] + sorted(roots)
    matches = []
    more = False
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
    timer = threading.Timer(SEARCH_SECONDS, process.kill)
    timer.start()
    try:
        for raw in process.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            filename, _sep, rest = line.partition(':')
            number, _sep, text = rest.partition(':')
            shown = _shown_path(filename, roots)
            if shown is None:
                continue
            if len(matches) >= limit:
                more = True
                break
            matches.append('%s:%s: %s' % (shown, number, text.strip()[:LINE_CHARS]))
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.wait()
    result = {'matches': matches, 'returned': len(matches), 'has_more': more}
    if more:
        result['next'] = 'narrow with module or file_types, or a longer query'
    return result


def _shown_path(filename, roots):
    real = os.path.realpath(filename)
    for root, name in roots.items():
        if real.startswith(root + os.sep):
            return name + '/' + os.path.relpath(real, root)
    return None


def list_modules(env, query=None, state='installed', limit=50, offset=0):
    domain = []
    if state != 'all':
        domain.append(('state', '=', state))
    if query:
        domain += ['|', ('name', 'ilike', query), ('shortdesc', 'ilike', query)]
    modules = env['ir.module.module'].sudo().search(domain, order='name')
    page = modules[offset:offset + limit]
    rows = []
    idx = index(env)
    for module in page:
        root = idx.module_root(module.name)
        rows.append({'name': module.name, 'title': module.shortdesc, 'version': module.latest_version or module.installed_version,
                     'state': module.state, 'folder': _folder(root)})
    more = len(modules) > offset + limit
    return {'modules': rows, 'returned': len(rows), 'total': len(modules), 'has_more': more,
            'next_offset': offset + limit if more else None, 'next': ['describe_module(module)']}


def _folder(root):
    if not root:
        return None
    parent = os.path.dirname(root)
    return '/'.join(parent.split(os.sep)[-2:])


def describe_module(env, name):
    if not isinstance(name, str) or not MODULE_NAME.match(name):
        raise ToolArgumentError("Unknown module: %s" % (name,))
    record = env['ir.module.module'].sudo().search([('name', '=', name)], limit=1)
    if not record:
        raise ToolArgumentError("Unknown module: %s" % name)
    manifest = get_manifest(name) or {}
    defines, extends, wizards = [], [], []
    for model_name in sorted(env.registry):
        klass = env.registry[model_name]
        own = [addon_of(base) for base in klass.__bases__ if not is_registry_class(base)]
        if name not in own:
            continue
        if klass._original_module == name:
            (wizards if klass._transient else defines).append(model_name)
        else:
            extends.append(model_name)
    env.cr.execute("""
        SELECT model, count(*), (array_agg(name ORDER BY name))[1:25]
          FROM ir_model_data WHERE module = %s
           AND model IN ('ir.ui.view', 'ir.ui.menu', 'ir.actions.act_window', 'ir.actions.server',
                         'ir.actions.report', 'ir.cron', 'ir.rule', 'ir.model.access', 'res.groups')
         GROUP BY model""", (name,))
    records = dict((model, {'count': count, 'xmlids': ['%s.%s' % (name, item) for item in names]})
                   for model, count, names in env.cr.fetchall())
    depends = record.dependencies_id.mapped('name')
    return {
        'name': name, 'title': record.shortdesc, 'state': record.state,
        'version': record.latest_version or manifest.get('version'), 'summary': record.summary,
        'author': record.author, 'folder': _folder(index(env).module_root(name)),
        'depends': depends, 'data_files': list(manifest.get('data', []))[:60],
        'models_defined': defines, 'models_extended': extends, 'wizards': wizards,
        'records': records,
        'next': ['describe_model(model)', 'search_code(query, module)', 'read_source("%s/__manifest__.py")' % name],
    }


def get_view(env, model_name, view_type='form', view=None, start_line=1, lines=300):
    model_class(env, model_name)
    model = env[model_name]
    view_id = None
    if view is not None:
        record = _view_record(env, view)
        if record.model != model_name:
            raise ToolArgumentError("View %s belongs to %s." % (view, record.model))
        view_id = record.id
        view_type = record.type
    try:
        arch = model.get_view(view_id, view_type)['arch']
    except ValueError as failure:
        raise ToolArgumentError('%s' % failure)
    from lxml import etree
    pretty = etree.tostring(etree.fromstring(arch), pretty_print=True, encoding='unicode').splitlines()
    total = len(pretty)
    start = max(1, start_line)
    window = pretty[start - 1:start - 1 + lines]
    end = start + len(window) - 1
    Views = env['ir.ui.view'].sudo()
    parts = Views.search([('model', '=', model_name), ('type', '=', view_type)], order='mode desc, priority, id', limit=60)
    xmlids = parts.get_external_id()
    result = {
        'model': model_name, 'view_type': view_type, 'start_line': start, 'end_line': end, 'total_lines': total,
        'arch': '\n'.join(window),
        'views': ['%s %s%s priority=%s%s' % (xmlids.get(part.id) or part.id, part.mode,
                                              ' inherits=%s' % (part.inherit_id.get_external_id().get(part.inherit_id.id) or part.inherit_id.id) if part.inherit_id else '',
                                              part.priority, '' if part.active else ' inactive') for part in parts],
    }
    if end < total:
        result['next'] = 'get_view(model, view_type, start_line=%s)' % (end + 1)
    return result


def _view_record(env, view):
    Views = env['ir.ui.view'].sudo()
    if isinstance(view, int) and not isinstance(view, bool):
        record = Views.browse(view).exists()
    elif isinstance(view, str) and '.' in view:
        record = env.ref(view, raise_if_not_found=False)
        record = record if record is not None and record._name == 'ir.ui.view' else Views
    else:
        record = Views
    if not record:
        raise ToolArgumentError("Unknown view: %s" % (view,))
    return record


def list_actions(env, model_name=None, query=None, limit=30):
    if model_name is None and not query:
        raise ToolArgumentError("Give model, query, or both.")
    if model_name is not None:
        model_class(env, model_name)
    rows = []
    Menus = env['ir.ui.menu']
    for kind, field in (('ir.actions.act_window', 'res_model'), ('ir.actions.server', 'model_name'),
                        ('ir.actions.report', 'model')):
        domain = []
        if model_name:
            domain.append((field, '=', model_name))
        if query:
            domain.append(('name', 'ilike', query))
        found = env[kind].sudo().search(domain, limit=limit + 1, order='id')
        xmlids = found.get_external_id()
        for action in found:
            entry = {'action': xmlids.get(action.id) or '%s,%s' % (kind, action.id), 'type': kind,
                     'name': action.name, 'model': action[field]}
            if kind == 'ir.actions.act_window':
                entry['view_mode'] = action.view_mode
                if action.domain and action.domain != '[]':
                    entry['domain'] = action.domain
            menus = Menus.search([('action', '=', '%s,%s' % (kind, action.id))], limit=5)
            if menus:
                entry['menus'] = menus.mapped('complete_name')
            rows.append(entry)
    more = len(rows) > limit
    return {'actions': rows[:limit], 'returned': len(rows[:limit]), 'has_more': more,
            'next': ['get_view(model, view_type)', 'search_xmlids(query)']}


def search_xmlids(env, query, model_name=None, limit=50):
    if not isinstance(query, str) or not query.strip():
        raise ToolArgumentError("query must be part of an external id, for example sale.view_order_form.")
    Data = env['ir.model.data'].sudo()
    module, dot, name = query.partition('.')
    if dot and MODULE_NAME.match(module):
        domain = [('module', '=', module), ('name', 'ilike', name)]
    else:
        domain = ['|', ('name', 'ilike', query), ('module', 'ilike', query)]
    if model_name:
        domain.append(('model', '=', model_name))
    found = Data.search(domain, limit=limit + 1, order='module, name')
    rows = ['%s.%s -> %s,%s' % (item.module, item.name, item.model, item.res_id) for item in found[:limit]]
    return {'xmlids': rows, 'returned': len(rows), 'has_more': len(found) > limit}


def overview(env, grant):
    """A short map of the database for the overview resource."""
    env.cr.execute("""
        SELECT name, latest_version, coalesce(author, '') FROM ir_module_module
         WHERE state = 'installed' ORDER BY name""")
    rows = env.cr.fetchall()
    others = ['%s %s' % (name, version or '') for name, version, author in rows if ODOO_AUTHOR not in author]
    return {
        'database': env.cr.dbname,
        'odoo': odoo.release.version,
        'installed_modules': len(rows),
        'modules_not_by_odoo': others[:150],
        'start_with': ['describe_model(model)', 'search_methods(query)', 'describe_module(module)',
                       'list_actions(model)'],
        'models_of_this_key': grant.models(env),
        'explore_code': grant.explore,
        'call_methods': grant.call_open,
    }
