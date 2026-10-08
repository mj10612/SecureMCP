"""Opt-in encrypted session transfer for separate CLI processes.

Only ciphertext is written to disk. MCP sessions remain memory-only.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import errno
import json
import math
import os
import re
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from secure_mcp.models import MaskMode, SurrogateStrategy, TokenMapping, TokenType
from secure_mcp.session import PrivacySession
from secure_mcp.engine.strategies import mapping_key, StrategyGenerator


_MAGIC = b"SecureMCP-session-v1\n"
_SALT_SIZE = 16
_LOCK_MAGIC = b"SecureMCP-native-lock-v1\n"


class SessionExpiredError(ValueError):
    """The snapshot is beyond its idle TTL and must not be reused."""


def _cipher(password: str, salt: bytes) -> Fernet:
    if not password:
        raise ValueError("A nonempty session password is required.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


class SnapshotEncryption:
    """One owner's derived key; each snapshot still gets fresh Fernet ciphertext.

    Keep this context only for the owner's lifetime. It retains no password and
    does not share encryption state with any other owner.
    """

    def __init__(self, password: str):
        self._salt = os.urandom(_SALT_SIZE)
        self._fernet = _cipher(password, self._salt)

    def encrypt(self, payload: bytes) -> bytes:
        return _MAGIC + self._salt + self._fernet.encrypt(payload)


def save_session(
    path: Path,
    session: PrivacySession,
    password: str,
    *,
    encryption: SnapshotEncryption | None = None,
) -> None:
    """Atomically write an authenticated, password-encrypted session snapshot."""
    with session.operation():
        payload = {
            "version": 2,
            "session_id": session.session_id,
            "mode": session.mode.value,
            "strategy": session.strategy.value,
            "ttl_seconds": session.ttl_seconds,
            "created_at": session.created_at,
            "last_accessed": session.last_accessed,
            "total_mask_calls": session.total_mask_calls,
            "total_unmask_calls": session.total_unmask_calls,
            "salt": session.generator.salt,
            "counters": {
                t.value: n for t, n in session.generator._type_counters.items()
            },
            "mappings": [
                m.model_dump(mode="json") for m in session.forward_store.values()
            ],
        }
    # The opt-in context owns the encryption key; ordinary CLI snapshots keep
    # their existing independent salt/key derivation per write.
    encrypted = (encryption or SnapshotEncryption(password)).encrypt(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    fd, temporary = tempfile.mkstemp(prefix=".secure-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_session(path: Path, password: str, session_id: str) -> PrivacySession:
    """Decrypt and validate a snapshot, rejecting expired or mismatched sessions."""
    data = path.read_bytes()
    if not data.startswith(_MAGIC) or len(data) <= len(_MAGIC) + _SALT_SIZE:
        raise ValueError("Invalid encrypted session file.")
    start = len(_MAGIC)
    salt = data[start : start + _SALT_SIZE]
    try:
        payload = json.loads(
            _cipher(password, salt).decrypt(data[start + _SALT_SIZE :])
        )
    except (InvalidToken, ValueError):
        raise ValueError(
            "Incorrect session password or damaged session file."
        ) from None
    if not isinstance(payload, dict):
        raise ValueError("Invalid encrypted session payload.")
    version = payload.get("version", 1)
    if version not in (1, 2):
        raise ValueError(f"Unsupported encrypted session payload version: {version}")
    try:
        session = PrivacySession(
            payload["session_id"],
            MaskMode(payload["mode"]),
            SurrogateStrategy(payload["strategy"]),
            payload["ttl_seconds"],
            payload["salt"],
        )
        session.created_at = payload["created_at"]
        session.last_accessed = payload["last_accessed"]
        session.total_mask_calls = payload["total_mask_calls"]
        session.total_unmask_calls = payload["total_unmask_calls"]
        if not all(
            isinstance(v, (int, float)) and math.isfinite(v)
            for v in (session.created_at, session.last_accessed)
        ):
            raise ValueError("Invalid timestamps")
        if not all(
            isinstance(v, int) and v >= 0
            for v in (session.total_mask_calls, session.total_unmask_calls)
        ):
            raise ValueError("Invalid counters")
        if not isinstance(payload["mappings"], list) or not isinstance(
            payload.get("counters", {}), dict
        ):
            raise ValueError("Invalid allocation state")
        for item in payload["mappings"]:
            mapping = TokenMapping.model_validate(item)
            if version == 1:
                mapping.format_version = 1
            key = mapping_key(
                mapping.original, mapping.token_type, mapping.context == "code"
            )
            if (
                not mapping.surrogate
                or key in session.forward_store
                or mapping.surrogate in session.reverse_store
            ):
                raise ValueError("Invalid mapping")
            # v1 bare pseudowords remain readable; new v2 values use delimiters.
            valid = bool(
                StrategyGenerator.get_pattern(session.strategy).fullmatch(
                    mapping.surrogate
                )
            )
            if (
                mapping.format_version == 1
                and session.strategy == SurrogateStrategy.PSEUDOWORD
            ):
                valid = bool(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", mapping.surrogate))
            if not valid:
                raise ValueError("Invalid surrogate format")
            session.forward_store[key] = mapping
            session.reverse_store[mapping.surrogate] = mapping
        session.generator.restore_state(list(session.reverse_store))
        for kind, count in payload.get("counters", {}).items():
            if not isinstance(count, int) or count < 0:
                raise ValueError("Invalid allocation counter")
            session.generator._type_counters[TokenType(kind)] = count
        expired = session.is_expired()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid encrypted session payload.") from None
    if session.session_id != session_id:
        session.clear()
        raise ValueError("Session file belongs to a different session ID.")
    if expired:
        session.clear()
        raise SessionExpiredError(
            "Session file has expired; mask again with a new session file."
        )
    session.touch()
    return session


@contextmanager
def session_file_lock(path: Path):
    """Guard a transaction with a crash-released, nonblocking OS advisory lock.

    The lock file is persistent: unlinking it can let concurrent processes lock
    different inodes. Legacy empty O_EXCL locks require explicit safe migration.
    """
    path = path.resolve()
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_RDWR)
    except FileNotFoundError:
        # Publish a fully initialized marker atomically. A crash between a bare
        # O_CREAT and marker write would otherwise strand an ambiguous empty file.
        initial_fd, temporary = tempfile.mkstemp(
            prefix=".secure-mcp-lock-", dir=lock.parent
        )
        try:
            with os.fdopen(initial_fd, "wb") as stream:
                stream.write(_LOCK_MAGIC)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, lock)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
        fd = os.open(lock, os.O_RDWR)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ValueError(
                    "Session file is in use. Retry after the other process finishes."
                ) from None
            raise
        if os.read(fd, len(_LOCK_MAGIC) + 1) != _LOCK_MAGIC:
            raise ValueError(
                "Legacy or unrecognized lock file: stop all old SecureMCP writers, "
                f"then manually remove '{lock}' before retrying. "
                "An active legacy writer cannot be distinguished safely from a crashed one."
            )
        yield
    finally:
        os.close(fd)  # The OS releases the lock, including on process termination.
