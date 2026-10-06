"""Shared offline contract for every gateway-compatible host adapter.

The same assertions run for each protocol dialect (Claude Messages, Codex
Responses). A new host adapter must pass this module before it may claim a
gateway protection path; see docs/COMPATIBILITY.md.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import urllib.error
import urllib.request

import pytest

from secure_mcp.gateway import GatewaySession, PrivacyGateway


SECRET_CODE = "def privateFunction(privateValue):\n    return privateValue + 42 # alice@example.com\n"


def claude_payload(text):
    return {
        "model": "fixture",
        "messages": [{"role": "user", "content": text}],
        "stream": True,
    }


def codex_payload(text):
    return {
        "model": "fixture",
        "input": [{"role": "user", "content": text}],
        "stream": True,
    }


DIALECTS = {
    "claude": ("messages", claude_payload),
    "codex": ("input", codex_payload),
}


@pytest.fixture(params=sorted(DIALECTS))
def dialect(request):
    return request.param


def test_contract_private_source_never_reaches_provider(dialect):
    session = GatewaySession()
    factory = claude_payload if dialect == "claude" else codex_payload
    masked = session.request(factory(SECRET_CODE))
    serialized = json.dumps(masked, ensure_ascii=False)
    for secret in ["privateFunction", "privateValue", "alice@example.com", "42"]:
        assert secret not in serialized


def test_contract_prompt_and_code_share_alias(dialect):
    session = GatewaySession()
    factory = claude_payload if dialect == "claude" else codex_payload
    masked = session.request(factory("Review privateFunction from alice@example.com"))
    serialized = json.dumps(masked, ensure_ascii=False)
    assert "privateFunction" not in serialized
    assert "alice@example.com" not in serialized
    alias = next(
        mapping.surrogate
        for mapping in session.session.forward_store.values()
        if mapping.original == "privateFunction"
    )
    assert alias in serialized
    restored = session.restore(serialized)
    assert "privateFunction" in restored
    assert "alice@example.com" in restored


def test_contract_tool_arguments_are_restored(dialect):
    session = GatewaySession()
    alias = session.mask("privateFunction", code=True)
    call = {
        "type": "tool_use" if dialect == "claude" else "function_call",
        "id": "call_1",
        "call_id": "call_1",
        "name": "Read",
        "input": {"file_path": alias},
    }
    if dialect == "codex":
        call["arguments"] = json.dumps({"file_path": alias})
        call.pop("input")
    restored = session.walk(call, restore=True)
    if dialect == "claude":
        assert restored["input"]["file_path"] == "privateFunction"
    else:
        assert json.loads(restored["arguments"])["file_path"] == "privateFunction"


def test_contract_tool_results_are_masked_on_followup(dialect):
    session = GatewaySession()
    result = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": f"file: {SECRET_CODE}",
    }
    masked = session.walk(result)
    assert "privateFunction" not in json.dumps(masked)
    assert "alice@example.com" not in json.dumps(masked)


def test_contract_split_sse_is_restored(dialect):
    session = GatewaySession()
    alias = session.mask("confidentialFunction", code=True)
    if dialect == "claude":
        events = [
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
        ]
    else:
        events = [
            {
                "type": "response.output_text.delta",
                "item_id": "m1",
                "delta": alias[:4],
            },
            {
                "type": "response.output_text.delta",
                "item_id": "m1",
                "delta": alias[4:],
            },
        ]
    body = "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events
    ).encode()
    restored = session.response(body, "text/event-stream").decode()
    assert "confidentialFunction" in restored
    assert alias not in restored


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "x", "input": [{"type": "input_image", "image_url": "raw"}]},
        {"model": "x", "input": [], "previous_response_id": "r1"},
        {"model": "x", "input": [], "unknown_field": "raw"},
    ],
)
def test_contract_unsupported_payloads_fail_closed(payload):
    with pytest.raises(ValueError):
        GatewaySession().request(payload)


def test_contract_schema_aliases_and_refs_resolve(dialect):
    session = GatewaySession()
    schema = {
        "type": "object",
        "properties": {
            "value": {"oneOf": [{"type": "string"}, {"type": "number"}]},
            "copy": {"$ref": "#/properties/value/oneOf/0"},
        },
    }
    masked = session.walk(schema)
    refs = [
        value["$ref"]
        for value in masked["properties"].values()
        if isinstance(value, dict) and "$ref" in value
    ]
    assert len(refs) == 1
    node = masked
    for segment in refs[0][2:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        node = node[int(segment)] if isinstance(node, list) else node[segment]
    assert node is not None


class StatusProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("retry-after", "7")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"raw upstream detail"}}')


def test_contract_local_token_and_api_key_boundary():
    gateway = PrivacyGateway(("127.0.0.1", 0), "contract-local-token")
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        raw = json.dumps(claude_payload("hello")).encode()
        for headers, expected in [
            ({}, 401),
            ({"X-SecureMCP-Token": "contract-local-token"}, 401),
            (
                {
                    "X-SecureMCP-Token": "contract-local-token",
                    "Authorization": "Bearer sk-ant-oat01-offline",
                    "x-api-key": "paid-key",
                },
                401,
            ),
        ]:
            request = urllib.request.Request(
                f"http://127.0.0.1:{gateway.server_port}/claude/v1/messages",
                raw,
                headers,
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            assert error.value.code == expected
            error.value.read()
            error.value.close()
    finally:
        gateway.shutdown()
        gateway.server_close()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_contract_provider_statuses_are_preserved(status):
    provider = ThreadingHTTPServer(("127.0.0.1", 0), StatusProvider)
    provider.status = status
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "contract-local-token",
        {"claude": upstream, "codex": upstream},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/claude/v1/messages",
            json.dumps(claude_payload("hello")).encode(),
            {
                "X-SecureMCP-Token": "contract-local-token",
                "Authorization": "Bearer sk-ant-oat01-offline",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == status
        body = error.value.read().decode()
        error.value.close()
        assert "raw upstream detail" not in body
        assert error.value.headers.get("retry-after") == "7"
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()
