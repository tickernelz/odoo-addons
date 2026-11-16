# -*- coding: utf-8 -*-
"""Cross-version helpers (Odoo 18, 19, 20).

* Odoo 18/19: Binary fields hold base64 text.
* Odoo 20+:   Binary fields hold a ``BinaryValue`` object with raw bytes.

Every binary read/write in the module goes through these helpers.
"""
import base64

try:  # Odoo 20+
    from odoo.tools.binary import BinaryBytes
except ImportError:  # Odoo 18 / 19
    BinaryBytes = None


def binary_to_bytes(value):
    if not value:
        return b""
    if hasattr(value, "content"):
        return value.content
    return base64.b64decode(value)


def bytes_to_field(data):
    if not data:
        return False
    if BinaryBytes is not None:
        return BinaryBytes(data)
    return base64.b64encode(data)


def attachment_bytes(attachment):
    """Raw bytes of an ir.attachment, whatever the storage format."""
    if not attachment:
        return b""
    raw = getattr(attachment, "raw", None)
    if raw:
        return raw if isinstance(raw, bytes) else binary_to_bytes(raw)
    datas = getattr(attachment, "datas", None)  # removed in Odoo 20
    return binary_to_bytes(datas) if datas else b""


def get_param(env, key, default=""):
    """System parameter as text. Odoo 20 replaced get_param() with typed getters."""
    icp = env["ir.config_parameter"].sudo()
    if hasattr(icp, "get_str"):
        return icp.get_str(key, default)
    return icp.get_param(key, default)


def raw_value(data):
    """Value for ir.attachment.raw: bytes in 18/19, BinaryBytes in 20."""
    return BinaryBytes(data) if BinaryBytes is not None else data
