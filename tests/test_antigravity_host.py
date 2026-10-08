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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

agy_binary = (
    os.environ.get("SECURE_MCP_AGY_BINARY")
    or shutil.which("agy")
    or shutil.which("agy.EXE")
)

agy_required = pytest.mark.skipif(
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


def test_sdk_transport_authentication_paths_and_lifetime():
    from secure_mcp.antigravity_sdk import antigravity_endpoint

    captured = []

    class LocalGateway(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append((self.path, body, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), LocalGateway)
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        with antigravity_endpoint(
            f"http://127.0.0.1:{gateway.server_port}", "t" * 32, "xai-fixture"
        ) as base_url:
            capability = base_url.split("/")[3]
            for url, method, status in [
                (
                    base_url.replace(capability, "wrong") + "/chat/completions",
                    "POST",
                    401,
                ),
                (base_url + "/responses", "POST", 400),
                (base_url + "/chat/completions", "GET", 400),
            ]:
                request = urllib.request.Request(url, b"secret fixture", method=method)
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                assert error.value.code == status
                response = error.value.read()
                assert capability.encode() not in response
                error.value.close()
            assert captured == []
            request = urllib.request.Request(
                base_url + "/chat/completions",
                b"{}",
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert json.load(response) == {"ok": True}
            path, body, headers = captured[0]
            assert path == "/xai/v1/chat/completions"
            assert body == b"{}"
            assert headers["X-Securemcp-Token"] == "t" * 32
            assert headers["Authorization"] == "Bearer xai-fixture"
            assert capability not in repr(captured)
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(request, timeout=2)
    finally:
        gateway.shutdown()
        gateway.server_close()


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.x.ai",
        "http://localhost:3000",
        "http://127.0.0.1:3000/private",
        "http://u:p@127.0.0.1:3000",
        "http://127.0.0.1:3000/?key=secret",
    ],
)
def test_sdk_transport_refuses_nonlocal_or_credential_origins(origin):
    from secure_mcp.antigravity_sdk import antigravity_endpoint

    with pytest.raises(ValueError, match="loopback"):
        with antigravity_endpoint(origin, "t" * 32, "xai-fixture"):
            pytest.fail("Unsupported origin accepted")


def test_sdk_transport_preserves_failclosed_gateway_boundary():
    from secure_mcp.antigravity_sdk import antigravity_endpoint
    from secure_mcp.gateway import LIMIT, PrivacyGateway

    captured = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            captured.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(500)
            self.end_headers()

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    Thread(target=provider.serve_forever, daemon=True).start()
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "t" * 32,
        api_upstreams={"xai": f"http://127.0.0.1:{provider.server_port}"},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    valid = {
        "model": "fixture",
        "messages": [{"role": "user", "content": "privateFunction alice@example.com"}],
    }
    try:
        with antigravity_endpoint(
            f"http://127.0.0.1:{gateway.server_port}", "t" * 32, "xai-fixture"
        ) as base_url:
            cases = [
                (
                    "/chat/completions",
                    json.dumps({**valid, "unknown": "raw secret"}).encode(),
                    {},
                    400,
                ),
                (
                    "/chat/completions",
                    json.dumps(
                        {
                            **valid,
                            "context_management": [
                                {"type": "compaction", "content": "raw secret"}
                            ],
                        }
                    ).encode(),
                    {},
                    400,
                ),
                ("/responses/compact", json.dumps(valid).encode(), {}, 400),
                ("/chat/completions", b'{"messages":', {}, 400),
                ("/chat/completions", b"{}", {"Content-Encoding": "gzip"}, 400),
                ("/chat/completions", b"{}", {"Content-Length": str(LIMIT + 1)}, 413),
            ]
            for suffix, body, headers, status in cases:
                request = urllib.request.Request(
                    base_url + suffix,
                    body,
                    {"Content-Type": "application/json", **headers},
                )
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                assert error.value.code == status
                assert b"raw secret" not in error.value.read()
                error.value.close()
        with antigravity_endpoint(
            f"http://127.0.0.1:{gateway.server_port}", "wrong" * 8, "xai-fixture"
        ) as base_url:
            request = urllib.request.Request(
                base_url + "/chat/completions",
                json.dumps(valid).encode(),
                {"Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            assert error.value.code == 401
            error.value.close()
        assert captured == []
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()


def test_sdk_transport_refuses_redirects_and_sanitizes_errors():
    from secure_mcp.antigravity_sdk import antigravity_endpoint

    received = []

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(self.path)
            self.send_response(307)
            self.send_header("Location", "/raw-private-error")
            self.end_headers()
            self.wfile.write(b"raw-private-error xai-fixture")

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        with antigravity_endpoint(
            f"http://127.0.0.1:{gateway.server_port}", "t" * 32, "xai-fixture"
        ) as base_url:
            request = urllib.request.Request(
                base_url + "/chat/completions",
                b"{}",
                {"Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            assert error.value.code == 502
            assert b"raw-private-error" not in error.value.read()
            error.value.close()
        assert received == ["/xai/v1/chat/completions"]
    finally:
        gateway.shutdown()
        gateway.server_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("korean", [False, True], ids=["english", "korean"])
async def test_sdk_gateway_native_file_read_roundtrip(tmp_path, monkeypatch, korean):
    import asyncio
    from secure_mcp.antigravity_sdk import antigravity_endpoint
    from secure_mcp.gateway import PrivacyGateway

    sdk = pytest.importorskip("google.antigravity")
    from google.antigravity.hooks import policy

    isolated_env(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fixture = workspace / "fixture.py"
    name = "비밀함수" if korean else "privateFunction"
    value = "비밀값" if korean else "privateValue"
    source = f"def {name}({value}):\n    return {value} + 42 # alice@example.com\n"
    fixture.write_text(source, encoding="utf-8")
    captured = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append((body, dict(self.headers)))
            session = gateway.sessions["xai"]
            if len(captured) == 1:
                function = next(
                    tool["function"]
                    for tool in body["tools"]
                    if session.restore(tool["function"]["name"]) == "view_file"
                )
                path_key = next(
                    key
                    for key in function["parameters"]["properties"]
                    if session.restore(key) == "AbsolutePath"
                )
                arguments = json.dumps(
                    {path_key: session.mask(str(fixture), code=True)}
                )
                split = len(arguments) // 2
                deltas = [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_fixture",
                                "type": "function",
                                "function": {
                                    "name": function["name"],
                                    "arguments": arguments[:split],
                                },
                            }
                        ],
                    },
                    {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": arguments[split:]}}
                        ]
                    },
                ]
                finish = "tool_calls"
            else:
                alias = next(
                    mapping.surrogate
                    for mapping in session.session.forward_store.values()
                    if "alice@example.com" in mapping.original
                )
                deltas = [{"content": alias[:5]}, {"content": alias[5:]}]
                finish = "stop"
            events = [
                {
                    "id": "chatcmpl_fixture",
                    "object": "chat.completion.chunk",
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
                for delta in deltas
            ]
            events.append(
                {
                    "id": "chatcmpl_fixture",
                    "object": "chat.completion.chunk",
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                }
            )
            result = (
                "".join("data: " + json.dumps(event) + "\n\n" for event in events)
                + "data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(result.encode())

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    Thread(target=provider.serve_forever, daemon=True).start()
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "t" * 32,
        api_upstreams={"xai": f"http://127.0.0.1:{provider.server_port}"},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        with antigravity_endpoint(
            f"http://127.0.0.1:{gateway.server_port}", "t" * 32, "xai-offline-fixture"
        ) as base_url:
            config = sdk.LocalOpenAIAgentConfig(
                model="fixture",
                base_url=base_url,
                workspaces=[str(workspace)],
                policies=[policy.deny("*"), policy.allow("view_file")],
                save_dir=str(tmp_path / "sdk-history"),
            ).lightweight()

            async def execute():
                output = []
                async with sdk.Agent(config) as agent:
                    question = (
                        "이 파일을 읽고 이메일을 알려줘"
                        if korean
                        else "Read this file and report its email"
                    ) + f": `{fixture}` ({name})"
                    response = await agent.chat(question)
                    async for token in response:
                        output.append(str(token))
                return "".join(output)

            answer = await asyncio.wait_for(execute(), timeout=40)
            capability = base_url.split("/")[3].encode()
            assert all(
                capability not in path.read_bytes()
                for path in (tmp_path / "sdk-history").rglob("*")
                if path.is_file()
            )
        assert len(captured) == 2
        for body, headers in captured:
            serialized = json.dumps(body, ensure_ascii=False)
            assert name not in serialized
            assert value not in serialized
            assert "alice@example.com" not in serialized
            assert headers["Authorization"] == "Bearer xai-offline-fixture"
            assert "x-securemcp-token" not in {key.lower() for key in headers}
        assert any(
            message.get("role") == "tool" for message in captured[1][0]["messages"]
        )
        assert "alice@example.com" in answer
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()


@agy_required
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
    assert json.loads(config_file(home).read_text(encoding="utf-8")) == {
        "mcpServers": {}
    }


@agy_required
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
@agy_required
async def test_agy_registered_command_offline_stdio_handshake(tmp_path, monkeypatch):
    """Launch the exact registered command with a real MCP client, no inference."""
    home = isolated_env(tmp_path, monkeypatch)
    added = agy("mcp", "add", "secure-mcp", sys.executable, "-m", "secure_mcp", "serve")
    assert added.returncode == 0, added.stderr
    entry = json.loads(config_file(home).read_text(encoding="utf-8"))["mcpServers"][
        "secure-mcp"
    ]
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
    sdk = pytest.importorskip(
        "google.antigravity", reason="Optional Antigravity SDK not installed"
    )
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
            self.wfile.write(
                b'{"error":{"message":"offline probe stop","type":"invalid_request_error"}}'
            )

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    Thread(target=provider.serve_forever, daemon=True).start()
    try:
        config = sdk.LocalOpenAIAgentConfig(
            model="fixture",
            base_url=f"http://127.0.0.1:{provider.server_port}/v1",
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


@agy_required
def test_agy_written_format_matches_shipped_example(tmp_path, monkeypatch):
    """The shipped example is accepted by the real CLI without modification."""
    isolated_env(tmp_path, monkeypatch)
    example = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "antigravity_mcp_config.json"
    )
    shipped = json.loads(example.read_text(encoding="utf-8"))["mcpServers"][
        "secure-mcp"
    ]
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
@agy_required
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
        assert completed.returncode == 0, (
            completed.stdout[-1500:],
            completed.stderr[-1000:],
        )
        assert "ENT" in completed.stdout or "NOUN" in completed.stdout, (
            completed.stdout[-1500:]
        )
    finally:
        agy("mcp", "remove", "secure-mcp")
    assert agy("mcp", "list").stdout.strip() == "No MCP servers configured."
