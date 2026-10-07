"""Shared offline contract for every gateway-compatible host adapter.

The same assertions run for each adapter (Claude Messages, Codex Responses,
xAI Responses). A new host adapter must pass this module before it may claim a
gateway protection path; see docs/COMPATIBILITY.md.

Daemon expiry and encrypted snapshot expiry are covered in test_gateway.py by
test_expired_gateway_session_is_not_revived_by_save and
test_expired_snapshot_is_discarded_without_losing_file. Installation/settings
preservation is covered by test_gateway_install_preserves_settings_login_and_uninstalls;
those lifecycle contracts are shared by the daemon rather than dialect-specific.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
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
    "xai": ("input", codex_payload),
}


@pytest.fixture(params=sorted(DIALECTS))
def dialect(request):
    return request.param


def test_contract_private_source_never_reaches_provider(dialect):
    session = GatewaySession()
    factory = claude_payload if dialect == "claude" else codex_payload
    masked = session.request(
        factory(SECRET_CODE), dialect="responses" if dialect == "xai" else None
    )
    serialized = json.dumps(masked, ensure_ascii=False)
    for secret in ["privateFunction", "privateValue", "alice@example.com"]:
        assert secret not in serialized
    # "42" is short: only a standalone occurrence means a real leak, since the
    # random numeric alias itself may contain those digits as a substring.
    assert not re.search(r"(?<!\d)42(?!\d)", serialized)


def test_contract_prompt_and_code_share_alias(dialect):
    session = GatewaySession()
    factory = claude_payload if dialect == "claude" else codex_payload
    prompt = "Review privateFunction and 고객조회 from alice@example.com"
    code = "def privateFunction(고객조회):\n    return 고객조회 # alice@example.com"
    payload = factory(prompt)
    if dialect == "claude":
        payload["messages"].append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": code}
                ],
            }
        )
    else:
        payload["input"].append(
            {"type": "function_call_output", "call_id": "t1", "output": code}
        )
    masked = session.request(payload, dialect="responses" if dialect == "xai" else None)
    serialized = json.dumps(masked, ensure_ascii=False)
    assert "privateFunction" not in serialized
    assert "alice@example.com" not in serialized
    alias = next(
        mapping.surrogate
        for mapping in session.session.forward_store.values()
        if mapping.original == "privateFunction"
    )
    assert alias in serialized
    if dialect == "claude":
        masked_prompt = masked["messages"][0]["content"]
        masked_code = masked["messages"][1]["content"][0]["content"]
    else:
        masked_prompt = masked["input"][0]["content"]
        masked_code = masked["input"][1]["output"]
    for name in ("privateFunction", "고객조회"):
        alias = session.session.forward_store[f"code:identifier:{name}"].surrogate
        assert alias in masked_prompt and alias in masked_code
    assert session.restore(masked_prompt) == prompt
    assert session.restore(masked_code) == code
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
    if dialect != "claude":
        call["arguments"] = json.dumps({"file_path": alias})
        call.pop("input")
        call.pop("id")
    else:
        call.pop("call_id")
    restored = session.walk(call, restore=True)
    if dialect == "claude":
        assert restored["input"]["file_path"] == "privateFunction"
    else:
        assert json.loads(restored["arguments"])["file_path"] == "privateFunction"


def test_contract_tool_results_are_masked_on_followup(dialect, tmp_path):
    session = GatewaySession()
    source = tmp_path / "private source.py"
    source.write_text(SECRET_CODE, encoding="utf-8")
    alias = session.mask(str(source), code=True)
    if dialect == "claude":
        call = {
            "type": "tool_use",
            "id": "t1",
            "name": "Read",
            "input": {"file_path": alias},
        }
        restored = session.walk(call, restore=True)
        local_path = restored["input"]["file_path"]
    else:
        call = {
            "type": "function_call",
            "call_id": "t1",
            "name": "read_file",
            "arguments": json.dumps({"file_path": alias}),
        }
        restored = session.walk(call, restore=True)
        local_path = json.loads(restored["arguments"])["file_path"]
    assert local_path == str(source)
    contents = Path(local_path).read_text(encoding="utf-8")
    if dialect == "claude":
        payload = claude_payload(
            [{"type": "tool_result", "tool_use_id": "t1", "content": contents}]
        )
    else:
        payload = codex_payload("")
        payload["input"] = [
            {"type": "function_call_output", "call_id": "t1", "output": contents}
        ]
    masked = session.request(payload, dialect="responses" if dialect == "xai" else None)
    assert "privateFunction" not in json.dumps(masked)
    assert "alice@example.com" not in json.dumps(masked)
    assert "t1" in json.dumps(masked)


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
    restored = session.response(
        body, "text/event-stream", dialect="responses" if dialect == "xai" else None
    ).decode()
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
def test_contract_unsupported_payloads_fail_closed(payload, dialect):
    with pytest.raises(ValueError):
        GatewaySession().request(
            payload, dialect="responses" if dialect == "xai" else None
        )


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


class RefreshProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, self.headers["Authorization"], payload))
        status = 401 if self.headers["Authorization"].endswith("expired") else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"expired"}' if status == 401 else b"{}")


def test_contract_refresh_preserves_aliases_and_provider_domains(tmp_path):
    provider = ThreadingHTTPServer(("127.0.0.1", 0), RefreshProvider)
    provider.requests = []
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "contract-local-token",
        {"claude": upstream, "codex": upstream},
        state_dir=tmp_path,
        api_upstreams={"xai": upstream},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        for agent, route, factory, prefix in [
            ("claude", "/claude/v1/messages", claude_payload, "Bearer sk-ant-oat01-"),
            ("codex", "/codex/responses", codex_payload, "Bearer eyJ"),
            ("xai", "/xai/v1/responses", codex_payload, "Bearer xai-"),
        ]:
            current = None
            for suffix, status in [("expired", 401), ("refreshed", 200)]:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{gateway.server_port}{route}",
                    json.dumps(factory(SECRET_CODE)).encode(),
                    {
                        "X-SecureMCP-Token": "contract-local-token",
                        "Authorization": prefix + suffix,
                    },
                )
                if status == 401:
                    with pytest.raises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    assert error.value.code == 401
                    error.value.close()
                    current = gateway.sessions[agent]
                else:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        assert response.status == 200
                    assert gateway.sessions[agent] is current
            first, second = provider.requests[-2:]
            assert first[1] == prefix + "expired"
            assert second[1] == prefix + "refreshed"
            assert first[2] == second[2]
            assert "privateFunction" not in json.dumps(second[2])
        claude, codex = gateway.sessions["claude"], gateway.sessions["codex"]
        assert claude is not codex
        assert claude.session.forward_store is not codex.session.forward_store
        # Aliases may have identical spelling across independent sessions.
        # Mutation and signed-reasoning provenance must remain provider-local.
        claude.mask("claudeOnlyFunction", code=True)
        codex.mask("codexOnlyFunction", code=True)
        assert "code:identifier:claudeOnlyFunction" not in codex.session.forward_store
        assert "code:identifier:codexOnlyFunction" not in claude.session.forward_store
        claude.opaque.add("a" * 64)
        assert "a" * 64 not in codex.opaque
        assert (tmp_path / "claude-symbols-v2.enc").exists()
        assert (tmp_path / "codex-symbols-v2.enc").exists()
        xai = gateway.sessions["xai"]
        assert xai.session.forward_store is not claude.session.forward_store
        assert xai.session.forward_store is not codex.session.forward_store
        assert "code:identifier:claudeOnlyFunction" not in xai.session.forward_store
        assert "code:identifier:codexOnlyFunction" not in xai.session.forward_store
        assert "a" * 64 not in xai.opaque
        xai.mask("xaiOnlyFunction", code=True)
        assert "code:identifier:xaiOnlyFunction" not in claude.session.forward_store
        assert "code:identifier:xaiOnlyFunction" not in codex.session.forward_store
        assert (tmp_path / "xai-symbols-v2.enc").exists()
        # A Claude credential is rejected before any Codex request reaches upstream.
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/codex/responses",
            json.dumps(codex_payload("hello")).encode(),
            {
                "X-SecureMCP-Token": "contract-local-token",
                "Authorization": "Bearer sk-ant-oat01-refreshed",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 401
        error.value.close()
        assert len(provider.requests) == 6
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()


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
def test_contract_provider_statuses_are_preserved(status, dialect):
    provider = ThreadingHTTPServer(("127.0.0.1", 0), StatusProvider)
    provider.status = status
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "contract-local-token",
        {"claude": upstream, "codex": upstream},
        api_upstreams={"xai": upstream},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        route, factory, auth = {
            "claude": (
                "/claude/v1/messages",
                claude_payload,
                "Bearer sk-ant-oat01-offline",
            ),
            "codex": ("/codex/responses", codex_payload, "Bearer eyJoffline"),
            "xai": ("/xai/v1/responses", codex_payload, "Bearer xai-offline"),
        }[dialect]
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}{route}",
            json.dumps(factory("hello")).encode(),
            {
                "X-SecureMCP-Token": "contract-local-token",
                "Authorization": auth,
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
