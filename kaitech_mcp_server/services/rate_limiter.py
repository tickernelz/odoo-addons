"""
Sliding-window rate limiter — zero external dependencies.

Configuration (via odoo.conf):
    mcp_rate_limit = 60          # total requests per window across all workers
    mcp_rate_window = 60         # window size in seconds

With multiple Odoo workers, the per-worker limit is automatically divided so the
total effective rate across all workers stays close to mcp_rate_limit.

Example (odoo.conf, workers=4, mcp_rate_limit=120):
    each worker allows 30 req/60s → total ≈ 120 req/60s

Buckets are cleaned up automatically when they expire to prevent memory leaks.

get_rate_limiter(env) returns a Redis-backed limiter when configured and available,
otherwise returns the module-level in-memory singleton.
"""

import logging
import threading
import time
from collections import deque

from odoo.tools import config as _odoo_config

_logger = logging.getLogger(__name__)

_DEFAULT_MAX = 60
_DEFAULT_WINDOW = 60  # seconds


def _resolve_limits() -> tuple[int, int]:
    """Compute per-worker rate limit from odoo.conf, adjusted for worker count."""
    total_max = int(_odoo_config.get("mcp_rate_limit", _DEFAULT_MAX) or _DEFAULT_MAX)
    window = int(_odoo_config.get("mcp_rate_window", _DEFAULT_WINDOW) or _DEFAULT_WINDOW)
    workers = int(_odoo_config.get("workers", 0) or 0)

    # Divide by worker count so total effective limit stays correct.
    # Single-process (workers=0): no adjustment needed.
    per_worker = max(10, total_max // max(1, workers)) if workers > 1 else total_max
    return per_worker, window


class RateLimiter:

    def __init__(self, max_requests: int | None = None, window_seconds: int | None = None):
        if max_requests is None or window_seconds is None:
            _max, _win = _resolve_limits()
            max_requests = max_requests if max_requests is not None else _max
            window_seconds = window_seconds if window_seconds is not None else _win

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, deque] = {}
        self._last_cleanup = time.monotonic()

    def check(self, key: str, max_override: int = 0) -> tuple[bool, int]:
        """Check whether `key` is within rate limit. Returns (allowed, remaining).

        max_override: per-user limit (> 0 overrides global default, 0 = use global).
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        effective_max = max_override if max_override > 0 else self.max_requests

        with self._lock:
            self._maybe_cleanup(now)
            bucket = self._buckets.setdefault(key, deque())

            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= effective_max:
                return False, 0

            bucket.append(now)
            return True, effective_max - len(bucket)

    def reconfigure(self, max_requests: int, window_seconds: int) -> None:
        """Update limits at runtime (e.g. from ir.config_parameter)."""
        with self._lock:
            self.max_requests = max_requests
            self.window_seconds = window_seconds
            self._buckets.clear()

    def _maybe_cleanup(self, now: float) -> None:
        """Purge fully-expired buckets every 5 minutes to avoid memory leak."""
        if now - self._last_cleanup < 300:
            return
        cutoff = now - self.window_seconds
        dead = [k for k, b in self._buckets.items() if not b or b[-1] < cutoff]
        for k in dead:
            del self._buckets[k]
        self._last_cleanup = now


# Module-level singleton — shared across all requests in this worker.
# Limits are resolved from odoo.conf at startup and worker-count-adjusted automatically.
rate_limiter = RateLimiter()

# ------------------------------------------------------------------ #
# Redis-backed limiter cache: keyed by redis_url so reconfiguration  #
# is cheap (reconnect only when the URL changes).                     #
# ------------------------------------------------------------------ #
_redis_limiter_cache: dict[str, object] = {}
_redis_cache_lock = threading.Lock()


def get_rate_limiter(env=None):
    """Return the active rate limiter for this request.

    If env is provided and Redis rate limiting is enabled in ir.config_parameter,
    returns a RedisRateLimiter instance (shared, one per redis_url per worker).
    On any configuration error or missing redis-py, returns the in-memory singleton.

    The Redis instance is cached at module level to avoid reconnecting per request.
    """
    if env is None:
        return rate_limiter

    try:
        ICP = env["ir.config_parameter"].sudo()
        redis_enabled = ICP.get_param("mcp_server.enable_redis_rate_limit", "False")
        if redis_enabled.lower() not in ("1", "true", "yes"):
            return rate_limiter

        redis_url = ICP.get_param("mcp_server.redis_url", "") or ""
        if not redis_url:
            _logger.warning(
                "MCP Server: Redis rate limiting enabled but mcp_server.redis_url is not set. "
                "Using in-memory limiter."
            )
            return rate_limiter

        max_req = int(ICP.get_param("mcp_server.rate_limit_per_minute", _DEFAULT_MAX) or _DEFAULT_MAX)
        window = int(ICP.get_param("mcp_server.rate_limit_window_seconds", _DEFAULT_WINDOW) or _DEFAULT_WINDOW)

        with _redis_cache_lock:
            cached = _redis_limiter_cache.get(redis_url)
            if cached is not None:
                # Update limits in case they were changed in settings
                cached.reconfigure(max_req, window)
                return cached

            from .redis_rate_limiter import RedisRateLimiter
            limiter = RedisRateLimiter(redis_url, max_req, window)
            _redis_limiter_cache[redis_url] = limiter
            return limiter

    except Exception:
        _logger.exception(
            "MCP Server: error resolving rate limiter from config. Using in-memory fallback."
        )
        return rate_limiter
