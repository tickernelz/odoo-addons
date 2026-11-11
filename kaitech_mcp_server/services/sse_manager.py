import queue
import threading
import uuid
from datetime import datetime


class McpServerSession:
    """Represents an active MCP client connection with its SSE message queue."""

    def __init__(self, uid: int, db: str, ip: str | None = None):
        self.session_id: str = str(uuid.uuid4())
        self.uid: int = uid
        self.db: str = db
        self.ip: str | None = ip
        self.message_count: int = 0
        self.last_activity: datetime = datetime.utcnow()
        self._queue: queue.Queue = queue.Queue()
        self.db_id: int | None = None  # mcp.session DB record id, set after audit create

    def put(self, data: dict) -> None:
        self.message_count += 1
        self.last_activity = datetime.utcnow()
        self._queue.put(data)

    def get(self, timeout: int = 25):
        return self._queue.get(timeout=timeout)

    def close(self) -> None:
        self._queue.put(None)


class SseSessionManager:
    """Thread-safe registry for active MCP SSE sessions and HTTP session id cache.

    HTTP sessions are stateless — each POST /mcp is independent — but we maintain
    a persistent mcp.session DB record per token for audit/logging. To avoid one
    extra DB search on every request we cache the token_id → session_db_id mapping
    in memory (per worker, which is fine for audit purposes).
    """

    def __init__(self):
        self._sessions: dict[str, McpServerSession] = {}
        # HTTP session cache: token_id (int) → mcp.session.id (int)
        self._http_cache: dict[int, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SSE session registry
    # ------------------------------------------------------------------

    def create(self, uid: int, db: str, ip: str | None = None) -> McpServerSession:
        session = McpServerSession(uid, db, ip)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> McpServerSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ------------------------------------------------------------------
    # HTTP session DB-id cache
    # ------------------------------------------------------------------

    def get_http_session_db_id(self, token_id: int) -> int | None:
        """Return cached mcp.session DB id for this token, or None if not cached."""
        with self._lock:
            return self._http_cache.get(token_id)

    def set_http_session_db_id(self, token_id: int, session_db_id: int) -> None:
        """Cache the mcp.session DB id for this token."""
        with self._lock:
            self._http_cache[token_id] = session_db_id

    def invalidate_http_session(self, token_id: int) -> None:
        """Remove a token from the HTTP session cache (e.g. on token revoke)."""
        with self._lock:
            self._http_cache.pop(token_id, None)


# Module-level singleton shared across all requests in this worker
session_manager = SseSessionManager()
