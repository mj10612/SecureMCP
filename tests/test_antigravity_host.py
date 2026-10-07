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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

agy_binary = (
    os.environ.get("SECURE_MCP_AGY_BINARY") or shutil.which("agy") or shutil.which("agy.EXE")
)

pytestmark = pytest.mark.skipif(
    not agy_binary,
    reason="Antigravity CLI (agy) not installed",
)


def isolated_env(tmp_path, monkeypatch):
    home = tmp_path / "agy home with spaces"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def agy(*args):
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


def test_agy_updates_named_entry_and_preserves_other_servers(tmp_path, monkeypatch):
    """The CLI replaces conflicts: save the original entry before installing."""
    home = isolated_env(tmp_path, monkeypatch)
    path = config_file(home)
    path.parent.mkdir(parents=True)
    original = {
        "mcpServers": {
            "other": {"command": "other-program", "args": ["arg with spaces"]},
            "secure-mcp": {"command": "previous-program", "args": ["old"]},
        }
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    command = str(tmp_path / "Python with spaces" / "python.exe")
    added = agy("mcp", "add", "secure-mcp", command, "-m", "secure_mcp", "serve")
    assert added.returncode == 0, added.stderr
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert written["mcpServers"]["secure-mcp"]["command"] == command
    assert written["mcpServers"]["secure-mcp"]["args"] == ["-m", "secure_mcp", "serve"]
    removed = agy("mcp", "remove", "secure-mcp")
    assert removed.returncode == 0, removed.stderr
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"other": original["mcpServers"]["other"]}
    }
    # Restoring a pre-existing entry is explicit; remove does not recover it.
    path.write_text(json.dumps(original), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == original


@pytest.mark.asyncio
async def test_agy_registered_command_offline_stdio_handshake(tmp_path, monkeypatch):
    """Launch the exact registered command with a real MCP client, no inference."""
    home = isolated_env(tmp_path, monkeypatch)
    added = agy("mcp", "add", "secure-mcp", sys.executable, "-m", "secure_mcp", "serve")
    assert added.returncode == 0, added.stderr
    entry = json.loads(config_file(home).read_text(encoding="utf-8"))["mcpServers"]["secure-mcp"]
    parameters = StdioServerParameters(command=entry["command"], args=entry["args"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "SecureMCP"
            tools = await session.list_tools()
            assert "mask_text" in {tool.name for tool in tools.tools}
            result = await session.call_tool("mask_text", {"text": "alice@example.com"})
            assert not result.is_error
            masked = json.loads(result.content[0].text)
            assert "alice@example.com" not in masked["masked_text"]
            assert masked["masked_tokens"] > 0


@pytest.mark.asyncio
async def test_sdk_local_endpoint_wire_format(tmp_path, monkeypatch):
    """Capture SDK traffic locally; this does not claim gateway protection."""
    sdk = pytest.importorskip("google.antigravity", reason="Optional Antigravity SDK not installed")
    isolated_env(tmp_path, monkeypatch)
    captured = []

    class Probe(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append((self.path, body, dict(self.headers)))
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"offline probe stop","type":"invalid_request_error"}}')

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    Thread(target=provider.serve_forever, daemon=True).start()
    try:
        config = sdk.LocalOpenAIAgentConfig(
            model="fixture", base_url=f"http://127.0.0.1:{provider.server_port}/v1",
            env={"OPENAI_API_KEY": "xai-offline-fixture"},
        ).lightweight()
        # Limit a failed external runtime so an optional probe cannot hang CI.
        import asyncio

        async def execute():
            async with sdk.Agent(config) as agent:
                response = await agent.chat("SyntheticFixture hello")
                async for _ in response:
                    pass

        with pytest.raises(Exception, match="offline probe stop"):
            await asyncio.wait_for(execute(), timeout=30)
        assert captured
        path, body, headers = captured[0]
        assert path == "/v1/chat/completions"
        assert body["model"] == "fixture"
        assert body["stream"] is True
        assert {"messages", "tools", "tool_choice"} <= body.keys()
        assert "SyntheticFixture hello" in json.dumps(body["messages"])
        # LocalOpenAIAgentConfig exposes no custom inference headers. The
        # gateway token must never be bypassed to make this path look supported.
        assert "x-securemcp-token" not in {name.lower() for name in headers}
    finally:
        provider.shutdown()
        provider.server_close()


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
