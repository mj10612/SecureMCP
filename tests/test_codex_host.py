"""Opt-in real Codex host test against an offline, deterministic fixture provider.

Set SECURE_MCP_CODEX_BINARY to a native Codex binary. No OpenAI credential or
external provider is used. Only freshly generated fixture hooks bypass trust.
"""

import json
import os
from pathlib import Path
import re
import subprocess
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from secure_mcp.hooks import configure

pytestmark = pytest.mark.skipif(
    not os.environ.get("SECURE_MCP_CODEX_BINARY"),
    reason="Native Codex binary not configured",
)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(payload)
        index = len(self.server.requests)
        if index == 1:
            item = {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": (
                            "Get-Content -LiteralPath fixture.txt"
                            if os.name == "nt"
                            else "cat fixture.txt"
                        ),
                        "yield_time_ms": 1000,
                    }
                ),
            }
        elif index == 2:
            combined = json.dumps(payload.get("input", []), ensure_ascii=False)
            tokens = re.findall(r"\u27e6ENT_\d+\u27e7", combined)
            if not tokens:
                self.send_error(500, "masked fixture token missing")
                return
            item = {
                "id": "fc_2",
                "type": "function_call",
                "call_id": "call_2",
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": (
                            f"Set-Content -LiteralPath restored.txt -Value '{tokens[-1]}'"
                            if os.name == "nt"
                            else f"printf '%s\\n' '{tokens[-1]}' > restored.txt"
                        ),
                        "yield_time_ms": 1000,
                    },
                    ensure_ascii=False,
                ),
            }
        else:
            item = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "Done.", "annotations": []}
                ],
            }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        response = {
            "id": f"resp_{index}",
            "object": "response",
            "status": "in_progress",
            "output": [],
        }
        events = [("response.created", {"response": response})]
        if item["type"] == "function_call":
            events += [
                (
                    "response.output_item.added",
                    {"output_index": 0, "item": {**item, "arguments": ""}},
                ),
                (
                    "response.function_call_arguments.delta",
                    {
                        "item_id": item["id"],
                        "output_index": 0,
                        "delta": item["arguments"],
                    },
                ),
                (
                    "response.function_call_arguments.done",
                    {
                        "item_id": item["id"],
                        "output_index": 0,
                        "arguments": item["arguments"],
                    },
                ),
            ]
        else:
            events += [
                (
                    "response.output_item.added",
                    {"output_index": 0, "item": {**item, "content": []}},
                ),
                (
                    "response.content_part.added",
                    {
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    },
                ),
                (
                    "response.output_text.delta",
                    {
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "Done.",
                    },
                ),
            ]
        events += [
            ("response.output_item.done", {"output_index": 0, "item": item}),
            (
                "response.completed",
                {"response": {**response, "status": "completed", "output": [item]}},
            ),
        ]
        for name, value in events:
            self.wfile.write(
                (
                    "event: "
                    + name
                    + "\ndata: "
                    + json.dumps({"type": name, **value}, ensure_ascii=False)
                    + "\n\n"
                ).encode()
            )
        self.wfile.flush()


def test_actual_codex_host_masks_and_restores(tmp_path):
    root = tmp_path
    binary = Path(os.environ["SECURE_MCP_CODEX_BINARY"])
    secret = "alice@example.com"
    requests = []
    home = root / "home"
    home.mkdir()
    (root / "fixture.txt").write_text(secret, encoding="utf-8")
    state = root / "state" / "codex"
    configure(home / "hooks.json", state, True, agent="codex")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.requests = requests
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (home / "config.toml").write_text(
        'model = "fixture"\nmodel_provider = "fixture"\napproval_policy = "never"\n'
        '[model_providers.fixture]\nname = "Offline fixture"\nwire_api = "responses"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        "requires_openai_auth = false\nsupports_websockets = false\n",
        encoding="utf-8",
    )
    env = {**os.environ, "CODEX_HOME": str(home)}
    # Only these freshly generated, locally inspected fixture hooks are trusted.
    command = [
        str(binary),
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "--dangerously-bypass-hook-trust",
        "-C",
        str(root),
        "Run the requested local fixture commands.",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, encoding="utf-8", env=env, timeout=60
        )
    finally:
        server.shutdown()
        server.server_close()
    assert completed.returncode == 0, completed.stderr[-5000:]
    assert len(requests) == 3
    assert (root / "restored.txt").read_text(encoding="utf-8-sig").strip() == secret
    assert all(secret not in json.dumps(req, ensure_ascii=False) for req in requests)
