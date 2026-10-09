"""Tests for SessionVault and PrivacySession isolation and safety."""

import time
from threading import TIMEOUT_MAX

import pytest
from secure_mcp.session import SessionVault
from secure_mcp.models import TokenType


@pytest.mark.parametrize(
    "interval",
    [0, -1, False, True, float("nan"), float("inf"), -float("inf"), TIMEOUT_MAX * 2],
)
def test_cleanup_interval_rejects_invalid_timeouts(interval):
    with pytest.raises(ValueError, match="cleanup_interval"):
        SessionVault(cleanup_interval=interval)


@pytest.mark.parametrize(
    "interval, expected",
    [(None, 0.25), (0.01, 0.01), (TIMEOUT_MAX, TIMEOUT_MAX)],
)
def test_cleanup_interval_preserves_valid_timeouts(interval, expected):
    vault = SessionVault(default_ttl=0.5, cleanup_interval=interval)
    try:
        assert vault.cleanup_interval == expected
        vault.get_or_create("valid_interval")
        assert vault._thread.is_alive()
    finally:
        vault.close()


def test_session_creation_and_isolation():
    vault = SessionVault(default_ttl=3600)
    sess_a = vault.get_or_create("session_a")
    sess_b = vault.get_or_create("session_b")

    sess_a.forward_store["secret_a"] = None
    sess_b.forward_store["secret_b"] = None

    assert "secret_a" in sess_a.forward_store
    assert "secret_a" not in sess_b.forward_store
    assert "secret_b" in sess_b.forward_store
    assert "secret_b" not in sess_a.forward_store


def test_session_wipe():
    vault = SessionVault(default_ttl=3600)
    sess = vault.get_or_create("session_purge")
    sess.forward_store["test"] = None

    cleared = vault.clear_session("session_purge")
    assert cleared
    assert vault.get_session("session_purge") is None


def test_session_ttl_expiration():
    vault = SessionVault(default_ttl=1)  # 1 second TTL
    sess = vault.get_or_create("quick_session")
    sess.ttl_seconds = 0.01

    time.sleep(0.05)
    assert sess.is_expired()
    assert vault.get_session("quick_session") is None


def test_session_sanitized_stats():
    vault = SessionVault()
    sess = vault.get_or_create("stats_session")
    gen = sess.generator
    surrogate = gen.generate(TokenType.ENTITY, "ConfidentialClient")
    from secure_mcp.models import TokenMapping

    sess.forward_store["ConfidentialClient"] = TokenMapping(
        original="ConfidentialClient", surrogate=surrogate, token_type=TokenType.ENTITY
    )

    stats = sess.get_stats()
    stats_json = stats.model_dump_json()

    # Raw secret must NEVER appear in stats
    assert "ConfidentialClient" not in stats_json
    assert stats.total_unique_mappings == 1
    assert stats.type_distribution.get("entity") == 1
