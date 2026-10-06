"""Antigravity CLI (`agy`) MCP integration, verified against the real binary.

All tests run inside an isolated home directory (USERPROFILE/HOME override),
so the user's real `~/.gemini` configuration is never touched. Tests skip
when the `agy` binary is unavailable. The live handshake test additionally
requires explicit opt-in because it consumes the existing subscription quota.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

agy_binary = (
    os.environ.get("SECURE_MCP_AGY_BINARY") or shutil.which("agy") or shutil.which("agy.EXE")
)

pytestmark = pytest.mark.skipif(
    not agy_binary,
    reason="Antigravity CLI (agy) not installed",
)


def isolated_env(tmp_path, monkeypatch):
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def agy(*args, home_env=True):
    return subprocess.run(
        [agy_binary, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def config_file(home):
    return home / ".gemini" / "config" / "mcp_config.json"


def test_agy_mcp_registration_cycle(tmp_path, monkeypatch):
    """add -> list -> remove leaves the isolated config exactly as found."""
    home = isolated_env(tmp_path, monkeypatch)
    assert agy("mcp", "list").stdout.strip() == "No MCP servers configured."

    added = agy("mcp", "add", "secure-mcp", sys.executable, "-m", "secure_mcp", "serve")
    assert added.returncode == 0, added.stderr
    assert "secure-mcp" in agy("mcp", "list").stdout

    written = json.loads(config_file(home).read_text(encoding="utf-8"))
    entry = written["mcpServers"]["secure-mcp"]
    assert entry["args"] == ["-m", "secure_mcp", "serve"]
    assert entry["command"].lower().endswith(("python.exe", "python"))

    removed = agy("mcp", "remove", "secure-mcp")
    assert removed.returncode == 0, removed.stderr
    assert agy("mcp", "list").stdout.strip() == "No MCP servers configured."
    assert json.loads(config_file(home).read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_agy_written_format_matches_shipped_example(tmp_path, monkeypatch):
    """The shipped example is accepted by the real CLI without modification."""
    isolated_env(tmp_path, monkeypatch)
    example = Path(__file__).resolve().parent.parent / "examples" / "antigravity_mcp_config.json"
    shipped = json.loads(example.read_text(encoding="utf-8"))["mcpServers"]["secure-mcp"]
    try:
        added = agy("mcp", "add", "secure-mcp", shipped["command"], *shipped["args"])
        assert added.returncode == 0, added.stderr
        assert "secure-mcp" in agy("mcp", "list").stdout
    finally:
        agy("mcp", "remove", "secure-mcp")


@pytest.mark.skipif(
    os.environ.get("SECURE_MCP_LIVE_SUBSCRIPTION") != "1",
    reason="Real subscription tests require explicit opt-in",
)
def test_agy_live_mcp_tool_handshake(tmp_path, monkeypatch):
    """End-to-end stdio handshake: the model calls mask_text on fixture data.

    Uses a synthetic fixture word only. MCP tool arguments are
    provider-visible by design; this test proves the handshake works, not
    that inference traffic is protected (see docs/COMPATIBILITY.md).
    """
    isolated_env(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    added = agy("mcp", "add", "secure-mcp", sys.executable, "-m", "secure_mcp", "serve")
    assert added.returncode == 0, added.stderr
    try:
        env = dict(os.environ)
        completed = subprocess.run(
            [
                agy_binary,
                "-p",
                "Use the secure-mcp mask_text tool on the word "
                "'FixturePatient' and report the masked_text value only.",
                "--model",
                "gemini-3.8-flash-low",
                "--dangerously-skip-permissions",
            ],
            capture_output=True,
            text=True,
            cwd=work,
            env=env,
            timeout=180,
        )
        assert completed.returncode == 0, (completed.stdout[-1500:], completed.stderr[-1000:])
        assert "ENT" in completed.stdout or "NOUN" in completed.stdout, completed.stdout[-1500:]
    finally:
        agy("mcp", "remove", "secure-mcp")
    assert agy("mcp", "list").stdout.strip() == "No MCP servers configured."
