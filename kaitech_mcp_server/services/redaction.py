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

import re

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


_QUOTED_OR_BARE = r"(\"[^\"]*\"|'[^']*'|[^\s&,;}\]\)]+)"
_LINE_RULES = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 " + _REDACTED),
    (
        re.compile(
            r"(?i)((?:proxy-)?authorization|set-cookie|cookie|x-api-key|x-auth-token)"
            r"([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\r\n]*)"
        ),
        r"\1\2" + _REDACTED,
    ),
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(session_id|db_password|admin_passwd|password|passwd|api_key|apikey|"
            r"access_token|refresh_token|client_secret|secret|token)([\"']?\s*[:=]\s*)" + _QUOTED_OR_BARE
        ),
        r"\1\2" + _REDACTED,
    ),
    (re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{12,}"), _REDACTED),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{16,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), _REDACTED),
)
_LONG_RUN = re.compile(r"[A-Za-z0-9_-]{32,}")


def redact_line(line: str) -> str:
    """line with credentials, cookies, session ids and key-like strings replaced by [REDACTED]."""
    for pattern, replacement in _LINE_RULES:
        line = pattern.sub(replacement, line)
    return _LONG_RUN.sub(_key_like, line)


def _key_like(match) -> str:
    text = match.group(0)
    if any(char.isdigit() for char in text) and any(char.isalpha() for char in text):
        return _REDACTED
    return text
