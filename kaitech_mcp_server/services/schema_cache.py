"""
In-process schema cache for MCP Server.

Stores computed values (model schemas, catalog, fields_get results) so that
repeated AI requests within the TTL window do not hit the ORM.

Design:
- Module-level dict protected by a threading.RLock.
- LRU eviction at MAX_ENTRIES via a deque of insertion-ordered keys.
- Hit/miss counters for observability.
- Full cache clear on ir.model / ir.model.fields mutations (see ir_model_invalidate.py).
"""

import logging
import threading
import time
from collections import deque

_logger = logging.getLogger(__name__)

_MAX_ENTRIES = 1000

_lock = threading.RLock()
# {cache_key: (timestamp_float, value)}
_store: dict[str, tuple[float, object]] = {}
# Ordered insertion keys — used for LRU eviction (leftmost = oldest)
_order: deque[str] = deque()

# Hit/miss counters (approximate under concurrency — good enough for ops)
_hits = 0
_misses = 0


# ---------------------------------------------------------------------------
# Config helpers (read from env on every call — cheap, avoids stale config)
# ---------------------------------------------------------------------------

def _cache_enabled(env) -> bool:
    ICP = env["ir.config_parameter"].sudo()
    return ICP.get_param("mcp_server.enable_schema_cache", "True").lower() in ("1", "true", "yes")


def _cache_ttl(env) -> int:
    ICP = env["ir.config_parameter"].sudo()
    try:
        return int(ICP.get_param("mcp_server.schema_cache_ttl_seconds", 300) or 300)
    except (ValueError, TypeError):
        return 300


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def get_or_compute(key: str, ttl_seconds: int, compute_fn):
    """Return cached value if present and fresh; otherwise call compute_fn() and cache it.

    Not env-aware by design — callers check _cache_enabled() before calling this.
    """
    global _hits, _misses

    with _lock:
        entry = _store.get(key)
        if entry is not None:
            ts, value = entry
            if time.monotonic() - ts <= ttl_seconds:
                _hits += 1
                return value
            # Stale — remove from store and order deque
            del _store[key]
            try:
                _order.remove(key)
            except ValueError:
                pass

    # Compute outside the lock to avoid holding it during potentially slow ORM work
    _misses += 1
    value = compute_fn()

    with _lock:
        _evict_if_needed()
        _store[key] = (time.monotonic(), value)
        _order.append(key)

    return value


def invalidate(prefix: str | None = None) -> int:
    """Clear cache entries.

    If prefix is given, only entries whose key starts with that prefix are removed.
    Returns the number of entries removed.
    """
    with _lock:
        if prefix is None:
            count = len(_store)
            _store.clear()
            _order.clear()
            return count

        to_delete = [k for k in _store if k.startswith(prefix)]
        for k in to_delete:
            del _store[k]
            try:
                _order.remove(k)
            except ValueError:
                pass
        return len(to_delete)


def get_stats() -> dict:
    """Return current cache statistics for the admin UI."""
    with _lock:
        total = len(_store)
        hits = _hits
        misses = _misses

    total_reqs = hits + misses
    hit_rate = round(hits / total_reqs * 100, 1) if total_reqs > 0 else 0.0
    return {
        "entries": total,
        "hits": hits,
        "misses": misses,
        "hit_rate_pct": hit_rate,
        "max_entries": _MAX_ENTRIES,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _evict_if_needed() -> None:
    """Remove the oldest entry when the store is at capacity. Called under _lock."""
    while len(_store) >= _MAX_ENTRIES and _order:
        oldest = _order.popleft()
        _store.pop(oldest, None)
