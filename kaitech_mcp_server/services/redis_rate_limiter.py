"""
Redis-backed distributed rate limiter using sorted-set sliding-window algorithm.

Falls back to the in-memory RateLimiter transparently on any Redis error so that
a Redis outage never blocks MCP requests.

Algorithm (per key):
  1. ZADD  mcp:ratelimit:{key}  score=now  member=uuid
  2. ZREMRANGEBYSCORE  ...  0  (now - window_seconds)   -- evict expired entries
  3. ZCARD  ...                                          -- count active entries
  4. EXPIRE ...  window_seconds                          -- auto-GC the key
  Steps 2-4 are wrapped in a pipeline for atomicity (no MULTI/EXEC needed here
  because ZADD is idempotent on replay; true atomicity would require a Lua script,
  but the pipeline reduces RTT to one round trip which is sufficient for this use
  case — worst-case over-count by 1 under race, acceptable for rate limiting).
"""

import logging
import time
import uuid

_logger = logging.getLogger(__name__)

# Lazy import — redis-py is optional
_redis_mod = None
_redis_import_failed = False


def _import_redis():
    global _redis_mod, _redis_import_failed
    if _redis_mod is not None:
        return _redis_mod
    if _redis_import_failed:
        return None
    try:
        import redis as _r
        _redis_mod = _r
        return _redis_mod
    except ImportError:
        _redis_import_failed = True
        _logger.warning(
            "MCP Server: redis-py package not installed. "
            "Install it with: pip install redis  "
            "Falling back to in-memory rate limiter."
        )
        return None


_KEY_PREFIX = "mcp:ratelimit:"


class RedisRateLimiter:
    """Distributed sliding-window rate limiter backed by Redis sorted sets.

    The interface is identical to RateLimiter so it can be used as a drop-in
    replacement in the controller.
    """

    def __init__(self, redis_url: str, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis_url = redis_url
        self._client = None
        self._fallback = None  # set on first connection failure

        redis_mod = _import_redis()
        if redis_mod is None:
            self._init_fallback()
            return

        try:
            # decode_responses=False keeps scores as floats, members as bytes — fine
            self._client = redis_mod.from_url(
                redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
            # Verify connectivity immediately so we fail fast at startup
            self._client.ping()
            _logger.info("MCP Server: Redis rate limiter connected to %s", redis_url)
        except Exception as exc:
            _logger.warning(
                "MCP Server: cannot connect to Redis (%s). "
                "Falling back to in-memory rate limiter.", exc
            )
            self._client = None
            self._init_fallback()

    def _init_fallback(self):
        from .rate_limiter import RateLimiter
        self._fallback = RateLimiter(
            max_requests=self.max_requests,
            window_seconds=self.window_seconds,
        )

    def check(self, key: str, max_override: int = 0) -> tuple[bool, int]:
        """Check rate limit. Returns (allowed, remaining).

        On any Redis error, falls back to the in-memory limiter.
        """
        if self._fallback is not None:
            return self._fallback.check(key, max_override=max_override)

        effective_max = max_override if max_override > 0 else self.max_requests
        redis_key = f"{_KEY_PREFIX}{key}"

        try:
            now = time.time()
            cutoff = now - self.window_seconds
            member = str(uuid.uuid4())

            pipe = self._client.pipeline(transaction=False)
            pipe.zadd(redis_key, {member: now})
            pipe.zremrangebyscore(redis_key, "-inf", cutoff)
            pipe.zcard(redis_key)
            pipe.expire(redis_key, self.window_seconds + 1)
            results = pipe.execute()

            count = results[2]  # result of ZCARD
            if count > effective_max:
                # Already over limit — remove the member we just added
                self._client.zrem(redis_key, member)
                return False, 0

            remaining = max(0, effective_max - count)
            return True, remaining

        except Exception as exc:
            _logger.warning(
                "MCP Server: Redis rate-limit check failed (%s). "
                "Activating in-memory fallback.", exc
            )
            self._init_fallback()
            return self._fallback.check(key, max_override=max_override)

    def reconfigure(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        if self._fallback is not None:
            self._fallback.reconfigure(max_requests, window_seconds)

    def ping(self) -> bool:
        """Return True if Redis is reachable. Used for the admin health-check button."""
        if self._client is None:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            return False
