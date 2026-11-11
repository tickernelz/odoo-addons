"""Explore tools: read-only introspection of the models, methods, code, modules, views and actions."""
from . import explore
from .grants import Grant, ToolArgumentError, ToolRefused


def check_arguments(definition, arguments):
    """Refuse arguments the tool schema does not list, and missing required ones."""
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object.")
    schema = definition['inputSchema']
    extra = sorted(set(arguments) - set(schema['properties']))
    if extra:
        raise ToolArgumentError("Unexpected arguments for %s: %s" % (definition['name'], ', '.join(extra)))
    missing = [key for key in schema.get('required', ()) if key not in arguments]
    if missing:
        raise ToolArgumentError("Missing arguments for %s: %s" % (definition['name'], ', '.join(missing)))


_READ_ONLY = {'readOnlyHint': True, 'openWorldHint': False}
_MODEL = {'type': 'string', 'description': 'Technical model name, for example res.partner.'}
_METHOD = {'type': 'string', 'description': 'Method name, for example action_confirm or _compute_amounts.'}
_PAGE = {'type': 'integer', 'minimum': 0, 'default': 0}

TOOL_DEFINITIONS = [
    {
        'name': 'describe_model',
        'title': 'Describe a model',
        'description': 'One call to understand a model: each field as one line (type, label, compute method with '
                       'its depends, related, selection keys, defining modules), defining and extending modules, '
                       'parent models, public methods, and what this token and its user may do.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Only these fields.'},
            'query': {'type': 'string', 'description': 'Keep fields and methods whose name contains this.'},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_methods',
        'title': 'Search methods',
        'description': 'Find methods by name, across all models or in one model (inherited ones included). '
                       'Private methods such as _compute_amount are included.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Words the method name contains, for example confirm.'},
            'model': _MODEL,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
            'offset': _PAGE,
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'describe_method',
        'title': 'Describe a method',
        'description': 'Signature, decorators, docstring, the fields it computes, and every override in MRO order '
                       '(module, file:line, calls super) of one method.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'method': _METHOD,
        }, 'required': ['model', 'method'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'get_method_source',
        'title': 'Method source',
        'description': 'Source code of a method and of every override, most derived first, with file and lines.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'method': _METHOD,
            'module': {'type': 'string', 'description': 'Only the definition in this module.'},
            'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 300},
        }, 'required': ['model', 'method'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_code',
        'title': 'Search code',
        'description': 'Search the code of the installed modules (or one module). Returns module/path:line: text.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Text to find; a regular expression when regex is true.'},
            'module': {'type': 'string'},
            'file_types': {'type': 'array', 'items': {'type': 'string', 'enum': list(explore.SOURCE_TYPES)},
                           'description': 'Default: py, xml, js, csv.'},
            'regex': {'type': 'boolean', 'default': False},
            'ignore_case': {'type': 'boolean', 'default': False},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'read_source',
        'title': 'Read a code file',
        'description': 'Read numbered lines of one code file of a module (py, xml, js, csv, po, pot).',
        'inputSchema': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'module/relative/path, for example sale/models/sale_order.py.'},
            'start_line': {'type': 'integer', 'minimum': 1, 'default': 1},
            'lines': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 200},
        }, 'required': ['path'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'list_modules',
        'title': 'List modules',
        'description': 'Modules of this database with title, version, state and folder.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Filter on name or title.'},
            'state': {'type': 'string', 'enum': ['installed', 'uninstalled', 'to upgrade', 'to install', 'all'],
                      'default': 'installed'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
            'offset': _PAGE,
        }, 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'describe_module',
        'title': 'Describe a module',
        'description': 'Manifest, dependencies, models a module defines or extends, its wizards, and the views, '
                       'menus, actions, crons, rules and groups it ships.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': {'type': 'string', 'description': 'Technical module name, for example sale.'},
        }, 'required': ['module'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'get_view',
        'title': 'Get a view',
        'description': "A model's view as this token's user sees it after every inheritance, and the views it is "
                       'built from.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'view_type': {'type': 'string', 'default': 'form', 'description': 'form, list, kanban, search, ...'},
            'view': {'description': 'A view xmlid or id instead of the default view.'},
            'start_line': {'type': 'integer', 'minimum': 1, 'default': 1},
            'lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 300},
        }, 'required': ['model'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'list_actions',
        'title': 'List actions',
        'description': 'Window, server and report actions of a model or matching a name, with the menus that open them.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'query': {'type': 'string', 'description': 'Filter on the action name.'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30},
        }, 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
    {
        'name': 'search_xmlids',
        'title': 'Search external ids',
        'description': 'Find external ids (module.name) and the record each points to.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Part of the id, or module.name_part.'},
            'model': _MODEL,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        }, 'required': ['query'], 'additionalProperties': False},
        'annotations': _READ_ONLY,
    },
]
_BY_NAME = dict((tool['name'], tool) for tool in TOOL_DEFINITIONS)
TOOL_NAMES = frozenset(_BY_NAME)


def _integer(arguments, name, default, lowest, highest):
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < lowest:
        raise ToolArgumentError("%s must be an integer of at least %s." % (name, lowest))
    if highest is not None and value > highest:
        raise ToolArgumentError("%s may be at most %s." % (name, highest))
    return value


def _flag(arguments, name):
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise ToolArgumentError("%s must be true or false." % name)
    return value


def _text(arguments, name, required=False):
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError("%s must be a non-empty string." % name)
    return value.strip()


def _names(arguments, name):
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("%s must be a list of names." % name)
    return value


def _describe_model(env, grant, arguments):
    return explore.describe_model(env, grant, _text(arguments, 'model', True), _names(arguments, 'fields'),
                                  _text(arguments, 'query'))


def _search_methods(env, grant, arguments):
    return explore.search_methods(env, _text(arguments, 'query', True), _text(arguments, 'model'),
                                  _integer(arguments, 'limit', 50, 1, 200), _integer(arguments, 'offset', 0, 0, None))


def _describe_method(env, grant, arguments):
    return explore.describe_method(env, _text(arguments, 'model', True), _text(arguments, 'method', True))


def _get_method_source(env, grant, arguments):
    return explore.method_source(env, _text(arguments, 'model', True), _text(arguments, 'method', True),
                                 _text(arguments, 'module'), _integer(arguments, 'max_lines', 300, 1, 1000))


def _search_code(env, grant, arguments):
    return explore.search_code(env, arguments.get('query'), _text(arguments, 'module'),
                               _names(arguments, 'file_types'), _flag(arguments, 'regex'),
                               _flag(arguments, 'ignore_case'), _integer(arguments, 'limit', 50, 1, 200))


def _read_source(env, grant, arguments):
    return explore.read_source(env, arguments.get('path'), _integer(arguments, 'start_line', 1, 1, None),
                               _integer(arguments, 'lines', 200, 1, 500))


def _list_modules(env, grant, arguments):
    state = arguments.get('state', 'installed')
    if state not in ('installed', 'uninstalled', 'to upgrade', 'to install', 'all'):
        raise ToolArgumentError("state must be installed, uninstalled, to upgrade, to install or all.")
    return explore.list_modules(env, _text(arguments, 'query'), state, _integer(arguments, 'limit', 50, 1, 200),
                                _integer(arguments, 'offset', 0, 0, None))


def _describe_module(env, grant, arguments):
    return explore.describe_module(env, _text(arguments, 'module', True))


def _get_view(env, grant, arguments):
    return explore.get_view(env, _text(arguments, 'model', True), _text(arguments, 'view_type') or 'form',
                            arguments.get('view'), _integer(arguments, 'start_line', 1, 1, None),
                            _integer(arguments, 'lines', 300, 1, 1000))


def _list_actions(env, grant, arguments):
    return explore.list_actions(env, _text(arguments, 'model'), _text(arguments, 'query'),
                                _integer(arguments, 'limit', 30, 1, 100))


def _search_xmlids(env, grant, arguments):
    return explore.search_xmlids(env, _text(arguments, 'query', True), _text(arguments, 'model'),
                                 _integer(arguments, 'limit', 50, 1, 200))


_IMPLEMENTATIONS = {
    'describe_model': _describe_model,
    'search_methods': _search_methods,
    'describe_method': _describe_method,
    'get_method_source': _get_method_source,
    'search_code': _search_code,
    'read_source': _read_source,
    'list_modules': _list_modules,
    'describe_module': _describe_module,
    'get_view': _get_view,
    'list_actions': _list_actions,
    'search_xmlids': _search_xmlids,
}


def visible(env, grant):
    """Names of the Explore tools this token can use."""
    return set(TOOL_NAMES) if grant is not None and grant.explore else set()


def _handler(name):
    def run_tool(env, args):
        grant = Grant.of(env)
        if grant is None or not grant.explore:
            raise ToolRefused("This token may not explore code. An administrator turns on Explore code on the token.")
        check_arguments(_BY_NAME[name], args)
        return _IMPLEMENTATIONS[name](env, grant, args)
    return run_tool


TOOL_HANDLERS = dict((name, _handler(name)) for name in _IMPLEMENTATIONS)
