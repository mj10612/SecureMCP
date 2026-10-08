"""Native hosts with fake OAuth and offline providers; never uses real accounts."""

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import subprocess
from threading import Thread

import pytest

from secure_mcp.gateway import PrivacyGateway, GatewayHandler, GatewaySession


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"models":[]}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((payload, dict(self.headers)))
        text = json.dumps(
            payload.get("messages", payload.get("input", [])), ensure_ascii=False
        )
        aliases = re.findall(r"private_symbol_\d+", text)
        alias = aliases[-1] if aliases else "missing-alias"
        if len(self.server.requests) > 1 and getattr(self.server, "tool_file", None):
            for session in self.server.gateway.sessions.values():
                found = next(
                    (
                        mapping.surrogate
                        for mapping in session.session.forward_store.values()
                        if "alice@example.com" in mapping.original
                    ),
                    None,
                )
                if found:
                    alias = found
        if self.path.split("?", 1)[0].endswith("count_tokens"):
            events = None
            body = b'{"input_tokens":100}'
        elif self.path.split("?", 1)[0].endswith("messages"):
            item = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 1},
            }
            events = [
                {"type": "message_start", "message": item},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": alias[:4]},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": alias[4:]},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
                {"type": "message_stop"},
            ]
        else:
            item = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": alias, "annotations": []}],
            }
            response = {
                "id": "resp_fixture",
                "object": "response",
                "status": "in_progress",
                "output": [],
            }
            events = [
                {"type": "response.created", "response": response},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "content": []},
                },
                {
                    "type": "response.content_part.added",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": alias[:4],
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": alias[4:],
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {**response, "status": "completed", "output": [item]},
                },
            ]
        if (
            events is not None
            and getattr(self.server, "tool_file", None)
            and len(self.server.requests) == 1
        ):
            agent = "claude" if self.path.split("?", 1)[0].endswith("messages") else "codex"
            mappings = self.server.gateway.sessions[
                agent
            ].session.forward_store.values()
            names = {m.original: m.surrogate for m in mappings}
            filename = names["fixture"] + "." + names["py"]
            if self.path.split("?", 1)[0].endswith("messages"):
                arguments = json.dumps({"file_path": filename})
                events = [
                    {"type": "message_start", "message": item},
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_read",
                            "name": "Read",
                            "input": {},
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": arguments,
                        },
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                        "usage": {"output_tokens": 1},
                    },
                    {"type": "message_stop"},
                ]
            else:
                args = json.dumps(
                    {
                        "cmd": "Get-Content -LiteralPath " + filename
                        if os.name == "nt"
                        else "cat " + filename,
                        "yield_time_ms": 1000,
                    }
                )
                call = {
                    "id": "fc_read",
                    "type": "function_call",
                    "call_id": "call_read",
                    "name": "exec_command",
                    "arguments": args,
                }
                events = [
                    {"type": "response.created", "response": response},
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {**call, "arguments": ""},
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": call["id"],
                        "output_index": 0,
                        "delta": args,
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": call["id"],
                        "output_index": 0,
                        "arguments": args,
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": call,
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            **response,
                            "status": "completed",
                            "output": [call],
                        },
                    },
                ]
        if events is not None:
            body = "".join(
                f"event: {e['type']}\ndata: "
                + json.dumps(e, ensure_ascii=False)
                + "\n\n"
                for e in events
            ).encode()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json" if events is None else "text/event-stream",
        )
        self.end_headers()
        self.wfile.write(body)


def fake_jwt():
    claim = {
        "exp": 4102444800,
        "email": "offline@example.invalid",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "offline-account",
            "chatgpt_plan_type": "plus",
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("=")
    return "eyJhbGciOiJub25lIn0." + encoded + ".offline"


@pytest.mark.parametrize(
    "agent,variable",
    [("claude", "SECURE_MCP_CLAUDE_BINARY"), ("codex", "SECURE_MCP_CODEX_BINARY")],
)
@pytest.mark.parametrize("use_tool", [False, True])
def test_native_subscription_cli_gateway(
    tmp_path, agent, variable, monkeypatch, use_tool
):
    if not os.environ.get(variable):
        pytest.skip("Native host not configured")
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.requests = []
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0), "offline-local-token", {"claude": upstream, "codex": upstream}
    )
    provider.gateway = gateway
    statuses = []
    original_send = GatewayHandler.send_body

    def record_send(handler, status, body, content_type="application/json"):
        statuses.append((handler.path, status, body.decode()[:300]))
        return original_send(handler, status, body, content_type)

    monkeypatch.setattr(GatewayHandler, "send_body", record_send)
    original_request = GatewaySession.request

    def record_request(session, payload, **kwargs):
        try:
            return original_request(session, payload, **kwargs)
        except Exception as exc:
            statuses.append(("mask error", str(exc), list(payload)))
            raise

    monkeypatch.setattr(GatewaySession, "request", record_request)
    Thread(target=gateway.serve_forever, daemon=True).start()
    work = tmp_path / "work"
    work.mkdir()
    source = work / "fixture.py"
    source.write_text(
        "def privateFunction(privateValue):\n    return privateValue + 42 # alice@example.com\n",
        encoding="utf-8",
    )
    provider.tool_file = source if use_tool else None
    home = tmp_path / "home"
    home.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANTHROPIC_", "CLAUDE_", "OPENAI_", "CODEX_"))
    }
    binary = os.environ[variable]
    env.update(
        {
            "HTTPS_PROXY": f"http://127.0.0.1:{gateway.server_port}",
            "HTTP_PROXY": f"http://127.0.0.1:{gateway.server_port}",
            "NO_PROXY": "localhost,127.0.0.1",
        }
    )
    secret = "privateFunction"
    prompt = f"Review `{secret}` in `fixture.py`.\n```python\ndef {secret}(privateValue):\n    return privateValue + 42\n```"
    if agent == "codex":
        env["CODEX_HOME"] = str(home)
        token = fake_jwt()
        (home / "auth.json").write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": None,
                    "tokens": {
                        "id_token": token,
                        "access_token": token,
                        "refresh_token": "offline-refresh",
                        "account_id": "offline-account",
                    },
                    "last_refresh": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        (home / "config.toml").write_text(
            'model = "fixture"\nmodel_provider = "fixture"\n'
            '[model_providers.fixture]\nname = "Offline fixture"\nwire_api = "responses"\n'
            f'base_url = "http://127.0.0.1:{gateway.server_port}/codex"\n'
            "requires_openai_auth = true\nsupports_websockets = false\n"
            'http_headers = { "X-SecureMCP-Token" = "offline-local-token" }\n'
            "[features]\napps = false\nplugins = false\n",
            encoding="utf-8",
        )
        command = [
            binary,
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "-C",
            str(work),
            "-",
        ]
        if use_tool:
            # Windows CI temp folders cannot provision the native sandbox. The
            # deterministic fixture only reads fixture.py; production setup never
            # changes the user's execution sandbox or approval policy.
            command[2:2] = ["--sandbox", "danger-full-access"]
    else:
        env.update(
            {
                "CLAUDE_CONFIG_DIR": str(home),
                "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-offline-fixture-only",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{gateway.server_port}/claude",
                "ANTHROPIC_CUSTOM_HEADERS": "X-SecureMCP-Token: offline-local-token",
            }
        )
        command = [
            binary,
            "--print",
            "--output-format",
            "json",
            "--tools",
            "Read" if use_tool else "",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--model",
            "claude-sonnet-4-6",
        ]
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            encoding="utf-8",
            cwd=work,
            env=env,
            timeout=45,
        )
        assert completed.returncode == 0, (
            statuses,
            completed.stdout[-1500:],
            completed.stderr[-1000:],
        )
        assert provider.requests, "No fixture inference request received"
        assert all(
            secret not in json.dumps(payload) for payload, _ in provider.requests
        )
        if use_tool:
            assert len(provider.requests) >= 2
            assert all(
                "alice@example.com" not in json.dumps(payload)
                for payload, _ in provider.requests
            )
            assert "alice@example.com" in completed.stdout
        session = gateway.sessions[agent]
        # The fixture returns a real alias; native output must contain its restored original.
        assert any(
            mapping.original in completed.stdout
            for mapping in session.session.forward_store.values()
        )
        for _, headers in provider.requests:
            assert headers["Authorization"].startswith(
                "Bearer sk-ant-oat" if agent == "claude" else "Bearer eyJ"
            )
            assert not any(key.lower() == "x-api-key" for key in headers)
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()
