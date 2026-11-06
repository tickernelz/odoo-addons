# -*- coding: utf-8 -*-
"""Read-only introspection of models, methods and addon source, cached per registry."""
import inspect
import io
import os
import threading

from odoo import models
from odoo.modules import module as odoo_module

SOURCE_TYPES = ('py', 'xml', 'js', 'csv', 'po')
SKIPPED_DIRECTORIES = ('lib', 'node_modules')
MAX_FILE_BYTES = 2 * 1024 * 1024
ADDON_PREFIXES = ('odoo.addons.', 'openerp.addons.')

_LOCK = threading.Lock()


def index(registry):
    """The Index of this registry; a module install or upgrade builds a new registry, so a new Index."""
    found = registry.__dict__.get('_kaitech_mcp_index')
    if found is None:
        with _LOCK:
            found = registry.__dict__.get('_kaitech_mcp_index')
            if found is None:
                found = Index(registry)
                registry._kaitech_mcp_index = found
    return found


def is_addon_class(cls):
    return (cls.__module__ or '').startswith(ADDON_PREFIXES) and hasattr(cls, '_module')


def parents_of(cls):
    parents = cls._inherit
    if isinstance(parents, basestring):
        return [parents]
    return list(parents or [])


def target_of(cls):
    """The model a class defines or extends, as Odoo's _build_model decides it."""
    parents = parents_of(cls)
    return cls._name or (len(parents) == 1 and parents[0]) or cls.__name__


def unwrap(function):
    seen = set()
    while hasattr(function, '__wrapped__') and id(function) not in seen:
        seen.add(id(function))
        function = function.__wrapped__
    return function


def is_framework_method(name):
    return hasattr(models.BaseModel, name)


class Definition(object):
    """One class body that defines a method, in the order Python resolves it."""

    def __init__(self, cls, name):
        self.cls = cls
        self.module = cls._module
        self.model = target_of(cls)
        self.function = unwrap(cls.__dict__[name])

    def location(self):
        try:
            path = inspect.getsourcefile(self.function)
            lines, start = inspect.getsourcelines(self.function)
        except (IOError, TypeError):
            return None, None, []
        return path, start, lines


class Index(object):
    """What the explore tools need to know about one registry, computed once."""

    def __init__(self, registry):
        self.registry = registry
        self._lock = threading.Lock()
        self._methods = {}
        self._own = None
        self._by_module = None
        self._files = {}
        self._roots = {}

    def modules(self):
        return sorted(self.registry._init_modules)

    def model_class(self, name):
        return self.registry[name]

    def methods(self, name):
        """{method: [Definition, ...]} over every addon class of the model, outermost override first."""
        found = self._methods.get(name)
        if found is not None:
            return found
        cls = self.model_class(name)
        field_names = cls._fields
        found = {}
        for klass in cls.__mro__:
            if not is_addon_class(klass):
                continue
            for attribute, value in klass.__dict__.items():
                if attribute.startswith('__') or attribute in field_names:
                    continue
                if not inspect.isfunction(unwrap(value)):
                    continue
                found.setdefault(attribute, []).append(Definition(klass, attribute))
        self._methods[name] = found
        return found

    def own_methods(self):
        """{method: [(model, [modules])]} for methods a model defines or overrides in its own classes."""
        if self._own is not None:
            return self._own
        with self._lock:
            if self._own is None:
                own = {}
                for name in sorted(self.registry.models):
                    for method, definitions in self.methods(name).items():
                        mine = [item.module for item in definitions if item.model == name]
                        if mine:
                            own.setdefault(method, []).append((name, mine))
                self._own = own
        return self._own

    def models_of(self, module):
        """(models the module defines, models it extends), sorted."""
        if self._by_module is None:
            with self._lock:
                if self._by_module is None:
                    by_module = {}
                    for name in self.registry.models:
                        cls = self.model_class(name)
                        by_module.setdefault(cls._original_module, (set(), set()))[0].add(name)
                        for klass in cls.__mro__:
                            if (is_addon_class(klass) and target_of(klass) == name
                                    and klass._module != cls._original_module):
                                by_module.setdefault(klass._module, (set(), set()))[1].add(name)
                    self._by_module = by_module
        defined, extended = self._by_module.get(module, ((), ()))
        return sorted(defined), sorted(extended)

    def module_root(self, module):
        """Real path of an installed module's directory, or None."""
        if module not in self.registry._init_modules:
            return None
        if module not in self._roots:
            path = odoo_module.get_module_path(module, display_warning=False)
            self._roots[module] = os.path.realpath(path) if path else None
        return self._roots[module]

    def relative(self, path):
        """module/relative/path for a real file inside an installed module, or None."""
        if not path:
            return None
        real = os.path.realpath(path)
        for module in self.registry._init_modules:
            root = self.module_root(module)
            if root and real.startswith(root + os.sep):
                return module + '/' + real[len(root) + 1:].replace(os.sep, '/')
        return None

    def resolve(self, relative):
        """Real path of module/relative/path when it is a source file inside an installed module, else None."""
        if not isinstance(relative, basestring) or '\x00' in relative:
            return None
        module, _slash, rest = relative.replace('\\', '/').lstrip('/').partition('/')
        root = self.module_root(module)
        if not root or not rest:
            return None
        real = os.path.realpath(os.path.join(root, *rest.split('/')))
        if not real.startswith(root + os.sep) or not os.path.isfile(real):
            return None
        if real.rsplit('.', 1)[-1].lower() not in SOURCE_TYPES:
            return None
        return real

    def files(self, module):
        """[(relative path, extension, real path)] of the source files of one installed module."""
        found = self._files.get(module)
        if found is not None:
            return found
        root = self.module_root(module)
        found = []
        if root:
            for directory, names, files in os.walk(root):
                names[:] = sorted(name for name in names
                                  if not name.startswith('.') and name not in SKIPPED_DIRECTORIES)
                for name in sorted(files):
                    extension = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
                    if extension not in SOURCE_TYPES or name.endswith('.min.js'):
                        continue
                    real = os.path.realpath(os.path.join(directory, name))
                    if not real.startswith(root + os.sep):
                        continue
                    relative = module + '/' + real[len(root) + 1:].replace(os.sep, '/')
                    found.append((relative, extension, real))
        self._files[module] = found
        return found


def read_text(real):
    if os.path.getsize(real) > MAX_FILE_BYTES:
        return None
    with io.open(real, 'r', encoding='utf-8', errors='replace') as handle:
        return handle.read()
