"""
Redaction helper for MCP audit log payload capture.

Performs a deep traversal of dicts and lists, replacing the values of any
key matching a configurable set of sensitive names with the literal string
"[REDACTED]". Keys are matched case-insensitively.

Pure stdlib — no Odoo dependency so this module is trivially unit-testable.

Example:
    >>> from services.redaction import redact
    >>> redact({"username": "alice", "password": "s3cr3t"})
    {'username': 'alice', 'password': '[REDACTED]'}
    >>> redact([{"api_key": "abc", "data": [{"token": "xyz"}]}])
    [{'api_key': '[REDACTED]', 'data': [{'token': '[REDACTED]'}]}]
"""

_DEFAULT_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd",
    "secret",
    "token",
    "api_key", "apikey",
    "authorization",
    "x-api-key",
    "ssn",
    "credit_card",
    "cvv",
})

_REDACTED = "[REDACTED]"


def redact(obj, sensitive_keys=None):
    """
    Deep-traverse *obj* (dict/list/primitive) and replace sensitive values.

    Args:
        obj: Any JSON-serializable value (dict, list, str, int, etc.).
        sensitive_keys: Optional iterable of lowercase key names to redact.
                        Defaults to the built-in sensitive key set.

    Returns:
        A new object with the same structure but sensitive values replaced.
        Non-dict/non-list scalars are returned unchanged.
    """
    if sensitive_keys is None:
        keys = _DEFAULT_SENSITIVE_KEYS
    else:
        keys = frozenset(k.lower() for k in sensitive_keys)

    return _traverse(obj, keys)


def _traverse(obj, keys):
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if k.lower() in keys else _traverse(v, keys))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_traverse(item, keys) for item in obj]
    return obj
