"""Thread-safe session vault managing ephemeral token substitution tables."""

from __future__ import annotations

import time
import uuid
from threading import Lock
from typing import Dict, Optional, Tuple

from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.models import (
    MaskMode,
    SessionStats,
    SurrogateStrategy,
    TokenMapping,
)


class PrivacySession:
    """An isolated privacy session holding bidirectional substitution tables."""

    def __init__(
        self,
        session_id: str,
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        ttl_seconds: int = 3600,
        salt: str = "",
    ):
        self.session_id = session_id
        self.mode = mode
        self.strategy = strategy
        self.ttl_seconds = ttl_seconds
        self.created_at = time.time()
        self.last_accessed = self.created_at
        self.total_mask_calls = 0
        self.total_unmask_calls = 0

        self.generator = StrategyGenerator(strategy=strategy, salt=salt or session_id)
        self.forward_store: Dict[str, TokenMapping] = {}
        self.reverse_store: Dict[str, TokenMapping] = {}

    def is_expired(self) -> bool:
        """Return True if session has exceeded its Time-To-Live."""
        return (time.time() - self.last_accessed) > self.ttl_seconds

    def touch(self) -> None:
        """Refresh last accessed timestamp."""
        self.last_accessed = time.time()

    def clear(self) -> None:
        """Securely wipe substitution tables from memory."""
        self.forward_store.clear()
        self.reverse_store.clear()

    def get_stats(self) -> SessionStats:
        """Return sanitized metrics with zero raw token disclosure."""
        type_counts: Dict[str, int] = {}
        for m in self.forward_store.values():
            cat = m.token_type.value
            type_counts[cat] = type_counts.get(cat, 0) + 1

        return SessionStats(
            session_id=self.session_id,
            created_at=self.created_at,
            last_accessed=self.last_accessed,
            total_unique_mappings=len(self.forward_store),
            total_mask_calls=self.total_mask_calls,
            total_unmask_calls=self.total_unmask_calls,
            mode=self.mode,
            strategy=self.strategy,
            type_distribution=type_counts,
        )


class SessionVault:
    """Thread-safe vault managing all active privacy sessions with automatic cleanup."""

    def __init__(self, default_ttl: int = 3600):
        self.default_ttl = default_ttl
        self._sessions: Dict[str, PrivacySession] = {}
        self._lock = Lock()
        self.default_session_id = "default_session"

    def get_or_create(
        self,
        session_id: Optional[str] = None,
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
    ) -> PrivacySession:
        """Get existing session or initialize a fresh one."""
        with self._lock:
            self._cleanup_expired_locked()

            sid = session_id.strip() if (session_id and session_id.strip()) else self.default_session_id
            if sid in self._sessions:
                sess = self._sessions[sid]
                sess.touch()
                return sess

            sess = PrivacySession(
                session_id=sid,
                mode=mode,
                strategy=strategy,
                ttl_seconds=self.default_ttl,
            )
            self._sessions[sid] = sess
            return sess

    def get_session(self, session_id: str) -> Optional[PrivacySession]:
        """Fetch session if present and not expired."""
        with self._lock:
            sid = session_id.strip() if session_id else self.default_session_id
            sess = self._sessions.get(sid)
            if sess:
                if sess.is_expired():
                    sess.clear()
                    del self._sessions[sid]
                    return None
                sess.touch()
                return sess
            return None

    def clear_session(self, session_id: str) -> bool:
        """Securely wipe a specific session."""
        with self._lock:
            sid = session_id.strip() if session_id else self.default_session_id
            if sid in self._sessions:
                self._sessions[sid].clear()
                del self._sessions[sid]
                return True
            return False

    def clear_all(self) -> int:
        """Clear all active sessions."""
        with self._lock:
            count = len(self._sessions)
            for sess in self._sessions.values():
                sess.clear()
            self._sessions.clear()
            return count

    def _cleanup_expired_locked(self) -> None:
        """Internal helper to prune expired sessions."""
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if (now - s.last_accessed) > s.ttl_seconds]
        for sid in expired:
            self._sessions[sid].clear()
            del self._sessions[sid]
