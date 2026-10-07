"""Optional real Grok Build test with synthetic credentials and a local provider."""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from secure_mcp.gateway import PrivacyGateway


class GrokProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload, dict(self.headers)))
        session = self.server.gateway.sessions["xai"]
        mappings = {
            m.original: m.surrogate for m in session.session.forward_store.values()
        }
        alias = next(
            (
                surrogate
                for original, surrogate in mappings.items()
                if "alice@example.com" in original
            ),
            "missing-alias",
        )
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
            "created_at": 1700000000,
            "status": "in_progress",
            "model": payload["model"],
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
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "delta": alias[:4],
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "delta": alias[4:],
            },
            {
                "type": "response.output_text.done",
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "text": alias,
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {
                    **response,
                    "status": "completed",
                    "output": [item],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            },
        ]
        if len(self.server.requests) == 1:
            read = next(
                (
                    tool
                    for tool in payload["tools"]
                    if session.restore(tool["name"]).lower() in {"read_file", "read"}
                ),
                None,
            )
            assert read is not None, [
                session.restore(tool["name"]) for tool in payload["tools"]
            ]
            self.server.read_tool_name = session.restore(read["name"])
            filename = mappings["fixture"] + "." + mappings["py"]
            properties = read["parameters"]["properties"]
            assert any(
                session.restore(key) in {"file_path", "path", "file", "target_file"}
                for key in properties
            ), {session.restore(key): value for key, value in properties.items()}
            parameter = next(
                key
                for key in properties
                if session.restore(key) in {"file_path", "path", "file", "target_file"}
            )
            arguments = json.dumps({parameter: filename})
            call = {
                "id": "fc_fixture",
                "type": "function_call",
                "call_id": "call_fixture",
                "name": read["name"],
                "arguments": arguments,
                "status": "completed",
            }
            events = [
                {"type": "response.created", "response": response},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**call, "arguments": ""},
                },
                *[
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": 0,
                        "item_id": call["id"],
                        "delta": part,
                    }
                    for part in (arguments[:10], arguments[10:])
                ],
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "item_id": call["id"],
                    "arguments": arguments,
                },
                {"type": "response.output_item.done", "output_index": 0, "item": call},
                {
                    "type": "response.completed",
                    "response": {
                        **response,
                        "status": "completed",
                        "output": [call],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 2,
                            "total_tokens": 12,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                },
            ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for index, event in enumerate(events):
            event["sequence_number"] = index
        self.wfile.write(
            "".join(
                f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
            ).encode()
        )


@pytest.mark.parametrize("language", ["en", "ko"])
def test_native_grok_responses_gateway(tmp_path, language):
    binary = os.environ.get("SECURE_MCP_GROK_BINARY")
    if not binary:
        pytest.skip("Native Grok Build host not configured")
    provider = ThreadingHTTPServer(("127.0.0.1", 0), GrokProvider)
    provider.requests = []
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "offline-local-token",
        upstreams={},
        api_upstreams={"xai": f"http://127.0.0.1:{provider.server_port}"},
    )
    provider.gateway = gateway
    for server in (provider, gateway):
        Thread(target=server.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "fixture.py").write_text(
        "def privateFunction(privateValue):\n    return privateValue # alice@example.com\n",
        encoding="utf-8",
    )
    prompt = (
        "Read fixture.py and return the email address in the file."
        if language == "en"
        else "fixture.py 파일을 읽고 파일에 있는 이메일 주소를 알려줘."
    )
    (home / "config.toml").write_text(
        '[models]\ndefault = "secure_mcp_xai"\n'
        '[model.secure_mcp_xai]\nmodel = "offline-fixture"\n'
        f'base_url = "http://127.0.0.1:{gateway.server_port}/xai/v1"\n'
        'env_key = "XAI_API_KEY"\napi_backend = "responses"\n'
        "supports_backend_search = false\nmax_retries = 0\n"
        'extra_headers = { "X-SecureMCP-Token" = "offline-local-token" }\n',
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GROK_", "XAI_"))
    }
    env.update(
        {
                "GROK_HOME": str(home),
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "XAI_API_KEY": "xai-offline-fixture",
            "HTTP_PROXY": f"http://127.0.0.1:{gateway.server_port}",
            "HTTPS_PROXY": f"http://127.0.0.1:{gateway.server_port}",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    for family in ("CURSOR", "CLAUDE"):
        for kind in ("SKILLS", "RULES", "AGENTS", "MCPS", "HOOKS"):
            env[f"GROK_{family}_{kind}_ENABLED"] = "0"
    try:
        completed = subprocess.run(
            [
                binary,
                "--single",
                prompt,
                "--model",
                "secure_mcp_xai",
                "--disable-web-search",
                "--no-subagents",
                "--max-turns",
                "3",
                "--allow",
                "read_file",
            ],
            capture_output=True,
            encoding="utf-8",
            cwd=work,
            env=env,
            timeout=45,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert provider.requests, (completed.stdout, completed.stderr)
        assert len(provider.requests) >= 2
        assert "alice@example.com" in completed.stdout
        outputs = [
            entry["output"]
            for _, payload, _ in provider.requests
            for entry in payload.get("input", [])
            if entry.get("type") == "function_call_output"
        ]
        assert outputs, "Grok did not return local tool output"
        restored = [gateway.sessions["xai"].restore(output) for output in outputs]
        assert any(
            "def privateFunction(privateValue):" in output
            and "alice@example.com" in output
            for output in restored
        )
        assert (work / "fixture.py").read_text(encoding="utf-8") == (
            "def privateFunction(privateValue):\n    return privateValue # alice@example.com\n"
        )
        for path, payload, headers in provider.requests:
            assert path == "/v1/responses"
            assert "alice@example.com" not in json.dumps(payload)
            assert "privateFunction" not in json.dumps(payload)
            assert "fixture.py" not in json.dumps(payload)
            assert payload["model"] == "offline-fixture"
            assert payload["stream"] is True
            assert headers["Authorization"] == "Bearer xai-offline-fixture"
            assert not any(key.lower() == "x-securemcp-token" for key in headers)
    finally:
        for server in (gateway, provider):
            server.shutdown()
            server.server_close()
