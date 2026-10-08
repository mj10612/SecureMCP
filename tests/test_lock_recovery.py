"""Transaction locks exclude live owners and recover after native owner crashes."""

import multiprocessing
from pathlib import Path
import time

import pytest

from secure_mcp.encrypted_session import session_file_lock


def _hold_lock(path, ready, release):
    with session_file_lock(Path(path)):
        ready.set()
        deadline = time.monotonic() + 20
        while not Path(release).exists() and time.monotonic() < deadline:
            time.sleep(0.01)


@pytest.mark.parametrize("crash", [False, True])
def test_process_lock_releases_after_exit_and_crash(tmp_path, crash):
    path = tmp_path / "session.enc"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = tmp_path / "release-owner"
    child = context.Process(target=_hold_lock, args=(str(path), ready, release))
    child.start()
    try:
        assert ready.wait(10), "Child did not acquire its transaction lock"
        with pytest.raises(ValueError, match="in use"):
            with session_file_lock(path):
                pytest.fail("Concurrent owner acquired a live transaction lock")
        if crash:
            child.terminate()
        else:
            release.touch()
        child.join(10)
        assert not child.is_alive()
        with session_file_lock(path):
            with pytest.raises(ValueError, match="in use"):
                with session_file_lock(path.parent / "." / path.name):
                    pytest.fail("Resolved equivalent path bypassed the lock")
        assert path.with_name("session.enc.lock").is_file()
    finally:
        release.touch()
        if child.is_alive():
            child.terminate()
        child.join(10)


def test_native_lock_is_reusable_after_transaction_failure(tmp_path):
    path = tmp_path / "session.enc"
    with pytest.raises(RuntimeError, match="fixture"):
        with session_file_lock(path):
            raise RuntimeError("fixture transaction failed")
    with session_file_lock(path):
        pass


def test_legacy_empty_lock_is_not_reclaimed_without_stopping_old_writers(tmp_path):
    path = tmp_path / "session.enc"
    lock = path.with_name("session.enc.lock")
    lock.write_bytes(b"")
    with pytest.raises(ValueError, match="stop all old SecureMCP writers"):
        with session_file_lock(path):
            pytest.fail("Legacy lock ownership was guessed")
    assert lock.read_bytes() == b""


def test_unrecognized_lock_marker_is_preserved(tmp_path):
    path = tmp_path / "session.enc"
    lock = path.with_name("session.enc.lock")
    lock.write_bytes(b"foreign-lock")
    with pytest.raises(ValueError, match="unrecognized lock"):
        with session_file_lock(path):
            pass
    assert lock.read_bytes() == b"foreign-lock"
