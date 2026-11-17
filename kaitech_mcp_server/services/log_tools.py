import os
import re
from datetime import datetime, timedelta

from odoo.tools import config

from .redaction import redact_line

TOOL_NAME = "server_log_tail"
DEFAULT_LINES = 100
MAX_LINES = 500
SCAN_BYTES = 8 * 1024 * 1024
MAX_LINE_CHARS = 2000
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \d+ ([A-Z_]+) \S+ ([^\s:]+): ")

TOOL_DEFINITIONS = [{
    "name": TOOL_NAME,
    "description": (
        "Read the last lines of the Odoo server log file named by the server configuration (no other file). "
        "Needs an admin-scope token of a Settings administrator. Filters: level (minimum), since_minutes, "
        "grep (case-insensitive text), logger (name or prefix, e.g. 'odoo.addons.kaitech_mcp_server'). "
        "At most 500 lines; 'truncated' is true when more matching lines exist. Bearer tokens, Authorization "
        "and Cookie headers, session ids, passwords, API keys, access tokens and key-like strings are "
        "replaced by [REDACTED] before grep runs."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lines": {"type": "integer", "default": DEFAULT_LINES, "minimum": 1, "maximum": MAX_LINES},
            "level": {"type": "string", "enum": list(LEVELS), "description": "Minimum level, e.g. 'WARNING'"},
            "since_minutes": {"type": "number", "description": "Only entries of the last N minutes"},
            "grep": {"type": "string", "description": "Keep entries containing this text (case-insensitive)"},
            "logger": {"type": "string", "description": "Logger name or prefix"},
        },
    },
}]


def log_file_path() -> str | None:
    """The log file the Odoo configuration names, if any."""
    return config["logfile"] or None


def tail(env, args: dict) -> dict:
    """The last matching entries of the server log, redacted."""
    if env.context.get("mcp_scope") != "admin" or not env.user._is_system():
        raise PermissionError(f"{TOOL_NAME} needs an admin-scope token of a Settings administrator.")
    wanted = _wanted(args)
    path = log_file_path()
    if not path:
        raise ValueError("Odoo writes its log to standard output on this server: no log file is configured.")
    text, reached_start = _read_end(path)
    entries = _entries(text.splitlines())
    cutoff = datetime.now() - timedelta(minutes=wanted["since_minutes"]) if wanted["since_minutes"] else None
    picked, used, truncated = [], 0, False
    for stamp, level, logger, lines in reversed(entries):
        if cutoff and stamp < cutoff:
            break
        if not _kept(wanted, level, logger):
            continue
        shown = [redact_line(line[:MAX_LINE_CHARS]) for line in lines]
        if wanted["grep"] and not any(wanted["grep"] in line.lower() for line in shown):
            continue
        if used >= wanted["lines"]:
            truncated = True
            break
        if len(shown) > wanted["lines"] - used:
            shown = shown[:wanted["lines"] - used]
            truncated = True
        truncated = truncated or any(len(line) > MAX_LINE_CHARS for line in lines)
        picked.append(shown)
        used += len(shown)
    picked.reverse()
    return {
        "log_file": path,
        "count": used,
        "lines": [line for entry in picked for line in entry],
        "truncated": truncated,
        "scanned_from_file_start": reached_start,
    }


TOOL_HANDLERS = {TOOL_NAME: tail}


def _wanted(args: dict) -> dict:
    lines = args.get("lines", DEFAULT_LINES)
    if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= MAX_LINES:
        raise ValueError(f"'lines' must be a whole number from 1 to {MAX_LINES}.")
    level = args.get("level")
    if level is not None and (not isinstance(level, str) or level.upper() not in LEVELS):
        raise ValueError(f"'level' must be one of {', '.join(LEVELS)}.")
    since = args.get("since_minutes")
    if since is not None and (isinstance(since, bool) or not isinstance(since, (int, float)) or since <= 0):
        raise ValueError("'since_minutes' must be a positive number.")
    for name in ("grep", "logger"):
        if args.get(name) is not None and not isinstance(args[name], str):
            raise ValueError(f"'{name}' must be text.")
    return {
        "lines": lines,
        "level": LEVELS.index(level.upper()) if level else None,
        "since_minutes": since,
        "grep": (args.get("grep") or "").lower(),
        "logger": args.get("logger") or "",
    }


def _kept(wanted: dict, level: str, logger: str) -> bool:
    if wanted["level"] is not None and (LEVELS.index(level) if level in LEVELS else 0) < wanted["level"]:
        return False
    return not wanted["logger"] or logger == wanted["logger"] or logger.startswith(wanted["logger"] + ".")


def _read_end(path: str) -> tuple:
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        start = max(0, size - SCAN_BYTES)
        handle.seek(start)
        data = handle.read(size - start)
    return data.decode("utf-8", "replace"), start == 0


def _entries(lines: list) -> list:
    entries = []
    for line in lines:
        header = _HEADER.match(line)
        if header:
            stamp = datetime.strptime(header.group(1), "%Y-%m-%d %H:%M:%S")
            entries.append((stamp, header.group(2), header.group(3), [line]))
        elif entries:
            entries[-1][3].append(line)
    return entries
