"""
MCP Module Generator — build_zip + validate_spec
=================================================

This service generates installable Odoo 19 modules from a JSON spec.
It is the backend engine for the ``odoo_generate_module`` MCP tool.

Spec Format
-----------
::

    {
      "technical_name": "my_module",
      "name": "My Module",
      "summary": "Short description",
      "description": "Long description",
      "category": "Productivity",
      "version": "19.0.1.0.0",
      "depends": ["base", "mail"],
      "license": "LGPL-3",
      "models": [
        {
          "name": "my.module.task",
          "description": "A task in my module",
          "inherit": null,
          "fields": [
            {"name": "name", "type": "char", "string": "Name", "required": true},
            {"name": "description", "type": "text", "string": "Description"},
            {"name": "user_id", "type": "many2one", "comodel": "res.users", "string": "Assigned To"},
            {"name": "state", "type": "selection",
             "selection": [["draft","Draft"],["done","Done"]], "default": "draft"},
            {"name": "date_due", "type": "date", "string": "Due Date"}
          ],
          "inherits_mail_thread": true,
          "rec_name": "name"
        }
      ],
      "menus": [
        {
          "name": "My Module",
          "sequence": 10,
          "children": [
            {"name": "Tasks", "action_model": "my.module.task"}
          ]
        }
      ],
      "security": {
        "groups": [
          {"name": "My Module User", "implied_by": "base.group_user"}
        ],
        "access_rights": [
          {"model": "my.module.task", "group": "base.group_user",
           "read": 1, "write": 1, "create": 1, "unlink": 0}
        ]
      },
      "views": {
        "auto_generate": true
      },
      "demo_data": []
    }

Field type allowlist
--------------------
char, text, html, integer, float, monetary, boolean, date, datetime,
selection, many2one, one2many, many2many, binary

Notes
-----
- Generated Python files are parsed by ``ast`` to catch syntax errors.
- ZIP uses deflate compression.
- ``validate_spec`` returns a list of human-readable error strings.
  An empty list means the spec is valid.
"""

import ast
import io
import keyword
import logging
import re
import zipfile

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_LICENSES = {"OPL-1", "LGPL-3", "AGPL-3"}

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+\.\d+$")

_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_MODEL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$")


def _normalize_model_name(name):
    """Coerce a model name to Odoo dotted-lowercase convention.

    Examples:
        'office.asset'  -> 'office.asset' (already valid)
        'OfficeAsset'   -> 'office.asset' (CamelCase)
        'officeAsset'   -> 'office.asset' (lowerCamelCase)
        'office_asset'  -> 'office.asset' (snake_case, no dot)

    Names that cannot be split unambiguously (e.g. 'officeasset')
    are returned unchanged and rejected by validate_spec.
    """
    if not isinstance(name, str) or not name:
        return name
    if _MODEL_NAME_RE.match(name):
        return name
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if "." not in snake and "_" in snake:
        snake = snake.replace("_", ".")
    return snake

_RESERVED_ODOO_NAMES = frozenset({
    "base", "web", "mail", "bus", "digest", "portal", "auth_signup",
    "sale", "purchase", "account", "stock", "mrp", "project", "hr",
    "crm", "website",
})

_RESERVED_FIELD_NAMES = frozenset({
    "id", "create_date", "write_date", "create_uid", "write_uid",
    "__last_update", "display_name",
})

_FIELD_TYPE_ALLOWLIST = frozenset({
    "char", "text", "html", "integer", "float", "monetary", "boolean",
    "date", "datetime", "selection", "many2one", "one2many", "many2many",
    "binary",
})

_RELATIONAL_TYPES = frozenset({"many2one", "one2many", "many2many"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_spec(spec: dict) -> list[str]:
    """Validate a module spec dict.

    Returns a list of human-readable error strings.  An empty list means
    the spec is valid and ``build_zip`` can proceed.
    """
    errors = []

    # ---- top-level required fields ----------------------------------------
    tech = spec.get("technical_name", "")
    if not tech:
        errors.append("technical_name is required.")
    else:
        if not _SNAKE_RE.match(tech):
            errors.append(
                f"technical_name {tech!r} must be snake_case "
                "(lowercase letters, digits, and underscores; must start with a letter)."
            )
        if keyword.iskeyword(tech):
            errors.append(f"technical_name {tech!r} is a reserved Python keyword.")
        if tech in _RESERVED_ODOO_NAMES:
            errors.append(
                f"technical_name {tech!r} conflicts with a built-in Odoo module name."
            )

    if not spec.get("name"):
        errors.append("name is required.")

    version = spec.get("version", "")
    if version and not _VERSION_RE.match(version):
        errors.append(
            f"version {version!r} must match pattern N.N.N.N.N "
            "(e.g. 19.0.1.0.0)."
        )

    license_val = spec.get("license", "LGPL-3")
    if license_val not in _VALID_LICENSES:
        errors.append(
            f"license {license_val!r} is not in the allowed list: "
            + ", ".join(sorted(_VALID_LICENSES))
        )

    depends = spec.get("depends", [])
    if depends is not None:
        if not isinstance(depends, list):
            errors.append("depends must be a list of strings.")
        else:
            for dep in depends:
                if not isinstance(dep, str) or not dep.strip():
                    errors.append(
                        f"Each entry in depends must be a non-empty string; got {dep!r}."
                    )

    # ---- models -------------------------------------------------------------
    models_spec = spec.get("models") or []
    if not isinstance(models_spec, list):
        errors.append("models must be a list.")
    else:
        seen_model_names: set[str] = set()
        for i, model in enumerate(models_spec):
            prefix = f"models[{i}]"
            if isinstance(model, dict) and "name" in model:
                model["name"] = _normalize_model_name(model["name"])
            if isinstance(model, dict) and "inherit" in model:
                model["inherit"] = _normalize_model_name(model["inherit"])
            mname = model.get("name", "") if isinstance(model, dict) else ""
            if not mname:
                errors.append(f"{prefix}: name is required.")
            elif not _MODEL_NAME_RE.match(mname):
                errors.append(
                    f"{prefix}: model name {mname!r} must be dotted lowercase "
                    "(e.g. my.module.task)."
                )
            else:
                if mname in seen_model_names:
                    errors.append(
                        f"{prefix}: duplicate model name {mname!r}."
                    )
                seen_model_names.add(mname)

            fields = model.get("fields") or []
            if not isinstance(fields, list):
                errors.append(f"{prefix}: fields must be a list.")
                continue

            seen_field_names: set[str] = set()
            for j, fld in enumerate(fields):
                fprefix = f"{prefix}.fields[{j}]"
                if isinstance(fld, dict) and "comodel" in fld:
                    fld["comodel"] = _normalize_model_name(fld["comodel"])
                fname = fld.get("name", "")
                if not fname:
                    errors.append(f"{fprefix}: name is required.")
                    continue
                if not _SNAKE_RE.match(fname):
                    errors.append(
                        f"{fprefix}: field name {fname!r} must be snake_case."
                    )
                if keyword.iskeyword(fname):
                    errors.append(
                        f"{fprefix}: field name {fname!r} is a reserved Python keyword."
                    )
                if fname in _RESERVED_FIELD_NAMES:
                    errors.append(
                        f"{fprefix}: field name {fname!r} is a reserved Odoo field name."
                    )
                if fname in seen_field_names:
                    errors.append(
                        f"{fprefix}: duplicate field name {fname!r} in model {mname!r}."
                    )
                seen_field_names.add(fname)

                ftype = fld.get("type", "")
                if not ftype:
                    errors.append(f"{fprefix}: type is required.")
                elif ftype not in _FIELD_TYPE_ALLOWLIST:
                    errors.append(
                        f"{fprefix}: field type {ftype!r} is not in the allowed list: "
                        + ", ".join(sorted(_FIELD_TYPE_ALLOWLIST))
                    )
                elif ftype in _RELATIONAL_TYPES and not fld.get("comodel"):
                    errors.append(
                        f"{fprefix}: relational field {fname!r} of type {ftype!r} "
                        "requires 'comodel'."
                    )
                elif ftype == "one2many" and not fld.get("inverse_field"):
                    errors.append(
                        f"{fprefix}: one2many field {fname!r} requires 'inverse_field'."
                    )
                elif ftype == "selection" and not fld.get("selection"):
                    errors.append(
                        f"{fprefix}: selection field {fname!r} requires 'selection' list."
                    )

    # ---- security: accept top-level access_rights/groups (Claude convention)
    # and merge into spec.security.* so downstream generators see one schema.
    top_ars = spec.get("access_rights")
    top_groups = spec.get("groups")
    if isinstance(top_ars, list) or isinstance(top_groups, list):
        sec = spec.get("security")
        if not isinstance(sec, dict):
            sec = {}
            spec["security"] = sec
        if isinstance(top_ars, list):
            existing = sec.get("access_rights") if isinstance(sec.get("access_rights"), list) else []
            sec["access_rights"] = existing + top_ars
        if isinstance(top_groups, list):
            existing = sec.get("groups") if isinstance(sec.get("groups"), list) else []
            sec["groups"] = existing + top_groups

    # ---- security: normalize model refs (no validation, just coercion) -----
    sec = spec.get("security")
    if isinstance(sec, dict):
        ars = sec.get("access_rights")
        if isinstance(ars, list):
            for ar in ars:
                if isinstance(ar, dict) and "model" in ar:
                    ar["model"] = _normalize_model_name(ar["model"])

    # ---- menus --------------------------------------------------------------
    menus = spec.get("menus") or []
    if not isinstance(menus, list):
        errors.append("menus must be a list.")
    else:
        for i, menu in enumerate(menus):
            if not menu.get("name"):
                errors.append(f"menus[{i}]: name is required.")

    return errors


def build_zip(spec: dict) -> tuple[bytes, list[str]]:
    """Generate an installable Odoo 19 module ZIP from *spec*.

    Returns ``(zip_bytes, warnings)`` where *zip_bytes* is the raw ZIP
    content and *warnings* is a list of non-fatal advisory strings.

    Raises ``ValueError`` if the spec fails validation or a generated
    Python file contains a syntax error.
    """
    errors = validate_spec(spec)
    if errors:
        raise ValueError(
            "Spec validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    warnings: list[str] = []
    tech = spec["technical_name"]
    models_spec = spec.get("models") or []
    security = spec.get("security") or {}
    if isinstance(security, list):
        security = {}
    views_cfg = spec.get("views") or {}
    if isinstance(views_cfg, list):
        views_cfg = {}
    auto_generate_views = views_cfg.get("auto_generate", True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # __init__.py (root)
        root_init = _gen_init_root(models_spec)
        _write_py(zf, f"{tech}/__init__.py", root_init, tech)

        # __manifest__.py
        manifest_src = _gen_manifest(spec)
        _write_py(zf, f"{tech}/__manifest__.py", manifest_src, tech)

        # models/__init__.py
        if models_spec:
            model_init = _gen_init_models(models_spec)
            _write_py(zf, f"{tech}/models/__init__.py", model_init, tech)

            for model in models_spec:
                model_src = _gen_model(model)
                fname = _model_name_to_file(model["name"])
                _write_py(zf, f"{tech}/models/{fname}.py", model_src, tech)

        # security/ir.model.access.csv
        access_csv = _gen_access_csv(spec)
        zf.writestr(f"{tech}/security/ir.model.access.csv", access_csv)

        # security/security.xml (groups)
        groups = security.get("groups") or []
        if groups:
            security_xml = _gen_security_groups(spec, tech)
            zf.writestr(f"{tech}/security/security.xml", security_xml)

        # views/<model>_views.xml
        if auto_generate_views and models_spec:
            for model in models_spec:
                view_xml = _gen_view(model, spec)
                fname = _model_name_to_file(model["name"])
                zf.writestr(f"{tech}/views/{fname}_views.xml", view_xml)

        # data/demo.xml
        demo_data = spec.get("demo_data") or []
        if demo_data:
            demo_xml = _gen_demo_data(spec)
            zf.writestr(f"{tech}/data/demo.xml", demo_xml)
        else:
            warnings.append(
                "No demo_data provided. Demo data is optional but recommended for testing."
            )

    return buf.getvalue(), warnings


# ---------------------------------------------------------------------------
# Internal generators
# ---------------------------------------------------------------------------


def _gen_manifest(spec: dict) -> str:
    tech = spec["technical_name"]
    name = spec.get("name", tech)
    summary = spec.get("summary", "")
    description = spec.get("description", "")
    category = spec.get("category", "Productivity")
    version = spec.get("version", "19.0.1.0.0")
    license_val = spec.get("license", "LGPL-3")
    author = spec.get("author", "MCP Generator")
    depends = spec.get("depends") or ["base"]
    if isinstance(depends, list):
        depends_repr = repr(depends)
    else:
        depends_repr = repr([d.strip() for d in depends.split(",") if d.strip()])

    models_spec = spec.get("models") or []
    security = spec.get("security") or {}
    if isinstance(security, list):
        security = {}
    groups = security.get("groups") or []
    views_cfg = spec.get("views") or {}
    if isinstance(views_cfg, list):
        views_cfg = {}
    auto_generate_views = views_cfg.get("auto_generate", True)
    demo_data = spec.get("demo_data") or []

    data_files: list[str] = ["'security/ir.model.access.csv'"]
    if groups:
        data_files.append("'security/security.xml'")
    if auto_generate_views and models_spec:
        for model in models_spec:
            fname = _model_name_to_file(model["name"])
            data_files.append(f"'views/{fname}_views.xml'")
    if demo_data:
        data_files.append("'data/demo.xml'")

    data_lines = ",\n        ".join(data_files)

    lines = [
        "# -*- coding: utf-8 -*-",
        "{",
        f"    'name': {name!r},",
        f"    'version': {version!r},",
        f"    'summary': {summary!r},",
        f"    'description': {description!r},",
        f"    'category': {category!r},",
        f"    'author': {author!r},",
        f"    'license': {license_val!r},",
        f"    'depends': {depends_repr},",
        "    'data': [",
        f"        {data_lines},",
        "    ],",
        "    'installable': True,",
        "    'application': False,",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _gen_init_root(models_spec: list) -> str:
    if models_spec:
        return "from . import models\n"
    return "# Nothing to import\n"


def _gen_init_models(models_spec: list) -> str:
    lines = []
    for model in models_spec:
        fname = _model_name_to_file(model["name"])
        lines.append(f"from . import {fname}")
    return "\n".join(lines) + "\n"


def _gen_model(model_spec: dict) -> str:
    model_name = model_spec["name"]
    description = model_spec.get("description", model_name)
    fields = model_spec.get("fields") or []
    inherits_mail = model_spec.get("inherits_mail_thread", False)
    rec_name = model_spec.get("rec_name")
    inherit = model_spec.get("inherit")

    class_name = _model_name_to_class(model_name)

    field_lines: list[str] = []
    for fld in fields:
        field_lines.extend(_gen_field_lines(fld))

    # Determine inheritance pattern:
    #   - inherit is a STRING and != model_name  -> extending existing model (no _name)
    #   - inherit is a LIST                      -> mixin pattern (NEW model + _name + _inherit)
    #   - inherit is None or empty               -> plain new model
    is_extending = isinstance(inherit, str) and inherit and inherit != model_name

    if is_extending:
        # Inheriting an existing model — extension pattern
        if inherits_mail:
            chain = [inherit, "mail.thread", "mail.activity.mixin"]
        else:
            chain = [inherit]
        inherit_line = (
            f"    _inherit = {chain[0]!r}"
            if len(chain) == 1
            else "    _inherit = [" + ", ".join(repr(x) for x in chain) + "]"
        )
    else:
        # New model — collect mixin inherits (either from inherit-as-list or inherits_mail_thread flag)
        mixins: list = []
        if isinstance(inherit, list):
            mixins.extend(x for x in inherit if isinstance(x, str) and x and x != model_name)
        if inherits_mail:
            for m in ("mail.thread", "mail.activity.mixin"):
                if m not in mixins:
                    mixins.append(m)
        inherit_line = (
            ("    _inherit = [" + ", ".join(repr(x) for x in mixins) + "]")
            if mixins else ""
        )

    # Compose class body
    parts: list[str] = []
    parts.append("from odoo import api, fields, models, _")
    parts.append("")
    parts.append("")
    parts.append(f"class {class_name}(models.Model):")

    if is_extending:
        parts.append(inherit_line)
    else:
        parts.append(f"    _name = {model_name!r}")
        parts.append(f"    _description = {description!r}")
        if inherit_line:
            parts.append(inherit_line)

    if rec_name:
        parts.append(f"    _rec_name = {rec_name!r}")

    parts.append("")

    for line in field_lines:
        parts.append("    " + line if line else "")

    # Remove trailing blank lines
    while parts and parts[-1].strip() == "":
        parts.pop()

    return "\n".join(parts) + "\n"


def _gen_field_lines(fld: dict) -> list[str]:
    """Return list of source lines for one field (no leading indent)."""
    fname = fld["name"]
    ftype = fld["type"]
    string = fld.get("string", fname.replace("_", " ").title())
    required = fld.get("required", False)
    readonly = fld.get("readonly", False)
    index = fld.get("index", False)
    default = fld.get("default")
    tracking = fld.get("tracking", False)
    help_text = fld.get("help", "")

    kwargs: list[str] = [f"string={string!r}"]

    if ftype == "char":
        field_cls = "fields.Char"
    elif ftype == "text":
        field_cls = "fields.Text"
    elif ftype == "html":
        field_cls = "fields.Html"
    elif ftype == "integer":
        field_cls = "fields.Integer"
    elif ftype == "float":
        field_cls = "fields.Float"
    elif ftype == "monetary":
        field_cls = "fields.Monetary"
    elif ftype == "boolean":
        field_cls = "fields.Boolean"
    elif ftype == "date":
        field_cls = "fields.Date"
    elif ftype == "datetime":
        field_cls = "fields.Datetime"
    elif ftype == "binary":
        field_cls = "fields.Binary"
    elif ftype == "selection":
        sel = fld.get("selection", [])
        field_cls = "fields.Selection"
        kwargs.insert(0, f"selection={sel!r}")
    elif ftype == "many2one":
        comodel = fld.get("comodel", "res.partner")
        ondelete = fld.get("ondelete", "set null")
        field_cls = "fields.Many2one"
        kwargs.insert(0, repr(comodel))
        kwargs.append(f"ondelete={ondelete!r}")
    elif ftype == "one2many":
        comodel = fld.get("comodel", "")
        inv = fld.get("inverse_field", "")
        field_cls = "fields.One2many"
        kwargs.insert(0, repr(comodel))
        kwargs.insert(1, repr(inv))
    elif ftype == "many2many":
        comodel = fld.get("comodel", "")
        field_cls = "fields.Many2many"
        kwargs.insert(0, repr(comodel))
    else:
        field_cls = "fields.Char"

    if required:
        kwargs.append("required=True")
    if readonly:
        kwargs.append("readonly=True")
    if index:
        kwargs.append("index=True")
    if tracking:
        kwargs.append("tracking=True")
    if default is not None:
        kwargs.append(f"default={default!r}")
    if help_text:
        kwargs.append(f"help={help_text!r}")

    args_str = ", ".join(kwargs)
    return [f"{fname} = {field_cls}({args_str})"]


def _gen_security_groups(spec: dict, tech: str) -> str:
    security = spec.get("security") or {}
    if isinstance(security, list):
        security = {}
    groups = security.get("groups") or []

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<odoo>",
        "",
    ]
    for grp in groups:
        gname = grp.get("name", "")
        implied = grp.get("implied_by", "")
        xml_id = _to_xml_id(gname)
        lines.append(f'    <record id="group_{xml_id}" model="res.groups">')
        lines.append(f'        <field name="name">{gname}</field>')
        if implied:
            lines.append(f'        <field name="implied_ids" eval="[(4, ref({implied!r}))]"/>')
        lines.append("    </record>")
        lines.append("")

    lines.append("</odoo>")
    return "\n".join(lines) + "\n"


def _gen_access_csv(spec: dict) -> str:
    security = spec.get("security") or {}
    if isinstance(security, list):
        security = {}
    access_rights = security.get("access_rights") or []
    models_spec = spec.get("models") or []

    rows: list[str] = [
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink"
    ]

    if access_rights:
        for ar in access_rights:
            model = ar.get("model", "")
            group = ar.get("group", "base.group_user")
            # Accept both Odoo-CSV-style keys (perm_*) and short keys
            r = ar.get("perm_read", ar.get("read", 1))
            w = ar.get("perm_write", ar.get("write", 1))
            c = ar.get("perm_create", ar.get("create", 1))
            u = ar.get("perm_unlink", ar.get("unlink", 0))
            model_xml_id = "model_" + model.replace(".", "_")
            row_id = ar.get("id") or (
                f"access_{model.replace('.', '_')}_{group.replace('.', '_')}"
            )
            row_name = ar.get("name") or f"{model} ({group})"
            rows.append(
                f"{row_id},{row_name},{model_xml_id},{group},{r},{w},{c},{u}"
            )
    else:
        # Auto-generate one admin-only line per model
        for model in models_spec:
            mname = model["name"]
            model_xml_id = "model_" + mname.replace(".", "_")
            row_id = f"access_{mname.replace('.', '_')}_admin"
            rows.append(
                f"{row_id},{mname} admin,{model_xml_id},base.group_system,1,1,1,1"
            )

    return "\n".join(rows) + "\n"


def _gen_view(model_spec: dict, spec: dict) -> str:
    """Generate list + form + search views + ir.actions.act_window."""
    model_name = model_spec["name"]
    tech = spec["technical_name"]
    fields = model_spec.get("fields") or []
    inherits_mail = model_spec.get("inherits_mail_thread", False)
    menus = spec.get("menus") or []

    # Derive IDs
    safe = model_name.replace(".", "_")
    list_id = f"{safe}_list_view"
    form_id = f"{safe}_form_view"
    search_id = f"{safe}_search_view"
    action_id = f"{safe}_action"

    # ---- list view ---------------------------------------------------------
    # First 6 scalar (non-relational, non-binary) fields
    scalar_types = {"char", "text", "integer", "float", "monetary", "boolean",
                    "date", "datetime", "selection"}
    list_fields = [f for f in fields if f.get("type") in scalar_types][:6]

    list_field_lines = "\n".join(
        f'            <field name="{f["name"]}"/>' for f in list_fields
    )
    if not list_field_lines:
        list_field_lines = ""

    # ---- form view ---------------------------------------------------------
    # Left column: char, many2one, text; right column: date, datetime, selection
    left_types = {"char", "many2one", "text", "html"}
    right_types = {"date", "datetime", "selection", "integer", "float",
                   "monetary", "boolean"}
    left_fields = [f for f in fields if f.get("type") in left_types]
    right_fields = [f for f in fields if f.get("type") in right_types]

    def _form_field_line(f):
        return f'                    <field name="{f["name"]}"/>'

    left_lines = "\n".join(_form_field_line(f) for f in left_fields)
    right_lines = "\n".join(_form_field_line(f) for f in right_fields)

    chatter_block = ""
    if inherits_mail:
        chatter_block = "\n        <chatter/>"

    # ---- search view -------------------------------------------------------
    search_char_fields = [f for f in fields
                          if f.get("type") in ("char", "text")][:4]
    selection_fields = [f for f in fields if f.get("type") == "selection"]

    search_field_lines = "\n".join(
        f'            <field name="{f["name"]}"/>' for f in search_char_fields
    )
    filter_lines = "\n".join(
        f'            <filter string="{f.get("string", f["name"])}" '
        f'name="filter_{f["name"]}" domain="[]"/>'
        for f in selection_fields
    )

    # ---- menu actions ------------------------------------------------------
    # Build menu XML for any menu entry that references this model
    menu_xml_parts: list[str] = []
    model_menus = _collect_model_menus(model_name, menus, tech)
    for entry in model_menus:
        menu_xml_parts.append(entry)

    menu_xml = "\n\n    ".join(menu_xml_parts)
    if menu_xml:
        menu_xml = "\n\n    " + menu_xml

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- List view -->
    <record id="{list_id}" model="ir.ui.view">
        <field name="name">{model_name}.list</field>
        <field name="model">{model_name}</field>
        <field name="arch" type="xml">
            <list>
{list_field_lines}
            </list>
        </field>
    </record>

    <!-- Form view -->
    <record id="{form_id}" model="ir.ui.view">
        <field name="name">{model_name}.form</field>
        <field name="model">{model_name}</field>
        <field name="arch" type="xml">
            <form>
                <sheet>
                    <group>
                        <group>
{left_lines}
                        </group>
                        <group>
{right_lines}
                        </group>
                    </group>
                </sheet>{chatter_block}
            </form>
        </field>
    </record>

    <!-- Search view -->
    <record id="{search_id}" model="ir.ui.view">
        <field name="name">{model_name}.search</field>
        <field name="model">{model_name}</field>
        <field name="arch" type="xml">
            <search>
{search_field_lines}
{filter_lines}
            </search>
        </field>
    </record>

    <!-- Action -->
    <record id="{action_id}" model="ir.actions.act_window">
        <field name="name">{model_spec.get("description", model_name)}</field>
        <field name="res_model">{model_name}</field>
        <field name="view_mode">list,form</field>
    </record>{menu_xml}

</odoo>
"""
    return xml


def _gen_demo_data(spec: dict) -> str:
    """Generate a stub demo.xml — actual records must be filled in post-generation."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<odoo>",
        "    <!-- Demo data -->",
        "</odoo>",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_py(zf: zipfile.ZipFile, arcname: str, source: str, tech: str) -> None:
    """Write *source* to *arcname* in *zf*, raising ValueError on syntax errors."""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(
            f"Syntax error in generated file {arcname!r}: {exc.msg} "
            f"(line {exc.lineno})\n\nSource:\n{source}"
        ) from exc
    zf.writestr(arcname, source)


def _model_name_to_file(model_name: str) -> str:
    """Convert 'my.module.task' -> 'my_module_task'."""
    return model_name.replace(".", "_")


def _model_name_to_class(model_name: str) -> str:
    """Convert 'my.module.task' -> 'MyModuleTask'."""
    return "".join(part.capitalize() for part in model_name.split("."))


def _to_xml_id(name: str) -> str:
    """Convert a display name to a safe XML ID fragment."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _collect_model_menus(model_name: str, menus: list, tech: str) -> list[str]:
    """Build ir.ui.menu XML strings for any menu entry whose action_model == model_name."""
    result: list[str] = []
    safe = model_name.replace(".", "_")
    action_id = f"{safe}_action"

    for top in menus:
        top_name = top.get("name", "")
        top_id = f"menu_{_to_xml_id(top_name)}"
        seq = top.get("sequence", 10)
        children = top.get("children") or []

        # Emit the top-level menu only if not already emitted
        # (first model referencing it wins; others will reference existing id)
        matching_children = [c for c in children
                             if c.get("action_model") == model_name]
        if not matching_children:
            continue

        result.append(
            f'<menuitem id="{top_id}" name="{top_name}" sequence="{seq}"/>'
        )
        for child in matching_children:
            child_name = child.get("name", model_name)
            child_id = f"menu_{_to_xml_id(top_name)}_{_to_xml_id(child_name)}"
            child_seq = child.get("sequence", 10)
            result.append(
                f'<menuitem id="{child_id}" name="{child_name}" '
                f'parent="{top_id}" '
                f'action="{action_id}" '
                f'sequence="{child_seq}"/>'
            )

    return result
