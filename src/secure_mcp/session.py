"""Session operation locks, bounded lifetimes, and periodic idle cleanup."""

from __future__ import annotations

import math
from threading import TIMEOUT_MAX, Event, RLock, Thread
import time
import uuid
import weakref

from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.models import MaskMode, SessionStats, SurrogateStrategy, TokenMapping


def validate_ttl(ttl: float) -> None:
    if isinstance(ttl, bool) or not math.isfinite(ttl) or not 0 < ttl <= 86400:
        raise ValueError("ttl_seconds must be positive and at most 86400 (one day).")


class SessionMappings(dict[str, TokenMapping]):
    """Public stores must be accessed inside session.operation() when mutated."""

    def __init__(self, generator: StrategyGenerator):
        super().__init__()
        self.generator = generator


class PrivacySession:
    def __init__(
        self,
        session_id: str,
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        ttl_seconds: float = 3600,
        salt: str = "",
    ):
        validate_ttl(ttl_seconds)
        self.session_id = session_id
        self.mode = mode
        self.strategy = strategy
        self.ttl_seconds = ttl_seconds
        self.created_at = self.last_accessed = time.time()
        self.total_mask_calls = self.total_unmask_calls = 0
        self.generator = StrategyGenerator(strategy=strategy, salt=salt)
        self.forward_store = SessionMappings(self.generator)
        self.reverse_store = SessionMappings(self.generator)

    def operation(self):
        """Use this context for counters, settings, and custom store mutations."""
        return self.generator.operation()

    def is_expired(self) -> bool:
        with self.generator.lock:
            return time.time() - self.last_accessed > self.ttl_seconds

    def touch(self) -> None:
        with self.generator.operation():
            self.last_accessed = time.time()

    def clear(self) -> None:
        with self.generator.lock:
            self.generator.closed = True
            self.forward_store.clear()
            self.reverse_store.clear()
            self.generator.used_surrogates.clear()
            self.generator._type_counters.clear()
            self.generator.salt = ""

    def get_stats(self) -> SessionStats:
        with self.operation():
            counts: dict[str, int] = {}
            for mapping in self.forward_store.values():
                name = mapping.token_type.value
                counts[name] = counts.get(name, 0) + 1
            return SessionStats(
                session_id=self.session_id,
                created_at=self.created_at,
                last_accessed=self.last_accessed,
                total_unique_mappings=len(self.forward_store),
                total_mask_calls=self.total_mask_calls,
                total_unmask_calls=self.total_unmask_calls,
                mode=self.mode,
                strategy=self.strategy,
                type_distribution=counts,
            )


class SessionVault:
    """Registry is locked; built-in engine operations share each session's RLock."""

    def __init__(
        self, default_ttl: float = 3600, cleanup_interval: float | None = None
    ):
        validate_ttl(default_ttl)
        self.default_ttl = default_ttl
        self.default_session_id = f"sess_{uuid.uuid4().hex}"
        self._sessions: dict[str, PrivacySession] = {}
        self._lock = RLock()
        self._stop = Event()
        self._closed = False
        self._thread: Thread | None = None
        self.cleanup_interval = (
            min(1.0, default_ttl / 2) if cleanup_interval is None else cleanup_interval
        )
        if (
            isinstance(self.cleanup_interval, bool)
            or not math.isfinite(self.cleanup_interval)
            or not 0 < self.cleanup_interval <= TIMEOUT_MAX
        ):
            raise ValueError(
                "cleanup_interval must be finite, positive, and at most threading.TIMEOUT_MAX"
            )

    def _sid(self, session_id: str | None) -> str:
        return (
            session_id.strip()
            if session_id and session_id.strip()
            else self.default_session_id
        )

    def _start_cleanup_locked(self) -> None:
        if self._thread is None:
            self._thread = Thread(
                target=self._sweep,
                args=(weakref.ref(self), self._stop, self.cleanup_interval),
                daemon=True,
            )
            self._thread.start()

    @staticmethod
    def _sweep(
        reference: weakref.ReferenceType[SessionVault], stop: Event, interval: float
    ) -> None:
        while not stop.wait(interval):
            vault = reference()
            if vault is None:
                return
            with vault._lock:
                vault._cleanup_expired_locked()
            del vault

    def get_or_create(
        self,
        session_id: str | None = None,
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
    ) -> PrivacySession:
        with self._lock:
            if self._closed:
                raise ValueError("Session vault is closed.")
            self._cleanup_expired_locked()
            sid = self._sid(session_id)
            if sid in self._sessions:
                session = self._sessions[sid]
                with session.operation():
                    if session.strategy != strategy:
                        raise ValueError(
                            f"Session '{sid}' uses strategy '{session.strategy.value}'; cannot switch to '{strategy.value}'. Use a new session ID."
                        )
                    # mode is a last-used policy, not an immutable allocation format.
                    session.mode = mode
                    session.touch()
                    return session
            return self.create(sid, mode, strategy, self.default_ttl)

    def create(
        self,
        session_id: str | None = None,
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        ttl_seconds: float | None = None,
    ) -> PrivacySession:
        ttl = self.default_ttl if ttl_seconds is None else ttl_seconds
        validate_ttl(ttl)
        with self._lock:
            if self._closed:
                raise ValueError("Session vault is closed.")
            self._cleanup_expired_locked()
            sid = self._sid(session_id)
            if sid in self._sessions:
                raise ValueError(
                    f"Session '{sid}' already exists. Use a new ID or clear it first."
                )
            session = PrivacySession(sid, mode, strategy, ttl)
            self._sessions[sid] = session
            self._start_cleanup_locked()
            return session

    def get_session(self, session_id: str) -> PrivacySession | None:
        with self._lock:
            self._cleanup_expired_locked()
            session = self._sessions.get(self._sid(session_id))
            if session:
                session.touch()
            return session

    @property
    def active_count(self) -> int:
        with self._lock:
            self._cleanup_expired_locked()
            return len(self._sessions)

    def clear_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(self._sid(session_id), None)
            if session:
                session.clear()  # waits for any active mask/unmask before invalidation
                return True
            return False

    def clear_all(self) -> int:
        with self._lock:
            count = len(self._sessions)
            for session in self._sessions.values():
                session.clear()
            self._sessions.clear()
            return count

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._closed = True
            self.clear_all()
        if self._thread:
            self._thread.join(timeout=2)

    def _cleanup_expired_locked(self) -> None:
        for sid in list(self._sessions):
            session = self._sessions[sid]
            with session.generator.lock:
                if session.is_expired():
                    session.clear()
                    del self._sessions[sid]
