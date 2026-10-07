"""Offline contract for the explicit xAI API mode (issue #66).

These tests never contact api.x.ai. They verify that API-key mode is opt-in,
isolated from subscription auth, and uses the same masking/restoration path.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
import urllib.error
import urllib.request

import pytest

from secure_mcp.gateway import GatewaySession, PrivacyGateway


class XaiProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload, dict(self.headers)))
        if getattr(self.server, "redirect", False):
            self.send_response(307)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/redirected"
            )
            self.end_headers()
            return
        if self.path.endswith("/chat/completions"):
            assert "system" not in payload
            assert payload["messages"][0]["role"] == "system"
            assert "store" not in payload
        text = json.dumps(
            payload.get("input", payload.get("messages", [])), ensure_ascii=False
        )
        value = text
        if self.path.endswith("/responses"):
            body = {
                "id": "resp_x",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": value}],
                    }
                ],
            }
        else:
            body = {
                "id": "chat_x",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": value},
                        "finish_reason": "stop",
                    }
                ],
            }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture()
def xai_gateway():
    provider = ThreadingHTTPServer(("127.0.0.1", 0), XaiProvider)
    provider.requests = []
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "xai-local-token",
        upstreams={},
        api_upstreams={"xai": upstream},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        yield gateway, provider
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()


def test_xai_responses_masks_and_restores(xai_gateway):
    gateway, provider = xai_gateway
    raw = "def privateFunction(privateValue):\n    return privateValue + 42 # alice@example.com"
    body = json.dumps(
        {
            "model": "grok-fixture",
            "input": [{"role": "user", "content": raw}],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
        body,
        {
            "X-SecureMCP-Token": "xai-local-token",
            "Authorization": "Bearer xai-fixture-key",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request) as response:
        restored = json.load(response)
    upstream_payload = json.dumps(provider.requests[-1][1])
    for secret in ["privateFunction", "privateValue", "alice@example.com"]:
        assert secret not in upstream_payload
    assert "privateFunction" in json.dumps(restored)
    assert provider.requests[-1][2]["Authorization"] == "Bearer xai-fixture-key"


def test_xai_chat_completions_roundtrip(xai_gateway):
    gateway, provider = xai_gateway
    raw = "고객조회 함수의 반환값을 확인해줘. 담당자 alice@example.com"
    body = json.dumps(
        {
            "model": "grok-fixture",
            "messages": [{"role": "user", "content": raw}],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/chat/completions",
        body,
        {
            "X-SecureMCP-Token": "xai-local-token",
            "Authorization": "Bearer xai-fixture-key",
        },
    )
    with urllib.request.urlopen(request) as response:
        restored = json.load(response)
    assert "alice@example.com" not in json.dumps(provider.requests[-1][1])
    assert "alice@example.com" in json.dumps(restored)


def test_xai_mode_is_disabled_by_default():
    gateway = PrivacyGateway(("127.0.0.1", 0), "no-xai-token")
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
            b"{}",
            {
                "X-SecureMCP-Token": "no-xai-token",
                "Authorization": "Bearer xai-fixture-key",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 400
        assert b"not enabled" in error.value.read()
        error.value.close()
    finally:
        gateway.shutdown()
        gateway.server_close()


def test_xai_rejects_subscription_and_missing_keys(xai_gateway):
    gateway, _ = xai_gateway
    for authorization in ["Bearer eyJfake", "", "Bearer sk-ant-oat01"]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
            b"{}",
            {
                "X-SecureMCP-Token": "xai-local-token",
                "Authorization": authorization,
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 401
        error.value.read()
        error.value.close()


def test_subscription_gateway_never_serves_xai_without_api_mode():
    # A subscription-installed gateway (default upstreams) must not expose xAI
    # routes, so subscription tokens cannot leak to a third-party origin.
    gateway = PrivacyGateway(("127.0.0.1", 0), "sub-token")
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
            b"{}",
            {
                "X-SecureMCP-Token": "sub-token",
                "Authorization": "Bearer eyJfake",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 400
        error.value.close()
    finally:
        gateway.shutdown()
        gateway.server_close()


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://api.x.ai",
        "https://api.x.ai/",
        "https://api.x.ai@evil.example",
        "http://127.0.0.1:42/path",
        "http://localhost:42",
        "http://127.0.0.1:42?redirect=evil",
    ],
)
def test_xai_upstream_origin_is_fixed(origin):
    with pytest.raises(ValueError):
        PrivacyGateway(
            ("127.0.0.1", 0), "token", upstreams={}, api_upstreams={"xai": origin}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"input": [], "unknown_field": "private"},
        {"input": [], "tools": [{"type": "web_search"}]},
        {"input": [], "tools": [{"type": "code_interpreter"}]},
        {"input": [], "previous_response_id": "provider-state"},
    ],
)
def test_xai_unsupported_requests_never_reach_provider(xai_gateway, payload):
    gateway, provider = xai_gateway
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
        json.dumps({"model": "fixture", **payload}).encode(),
        {"X-SecureMCP-Token": "xai-local-token", "Authorization": "Bearer xai-fixture"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 400
    error.value.close()
    assert provider.requests == []


@pytest.mark.parametrize(
    "dialect, options",
    [
        ("responses", {"reasoning": {"effort": "low", "private": "alice@example.com"}}),
        (
            "chat_completions",
            {"max_completion_tokens": {"private": "alice@example.com"}},
        ),
        ("responses", {"stream": "alice@example.com"}),
        ("responses", {"store": 1}),
        ("responses", {"parallel_tool_calls": []}),
        ("responses", {"max_output_tokens": True}),
        ("responses", {"max_output_tokens": 0}),
        ("responses", {"temperature": float("nan")}),
        ("responses", {"temperature": float("inf")}),
        ("responses", {"temperature": True}),
        ("responses", {"top_p": 1.1}),
        ("responses", {"service_tier": "alice@example.com"}),
        ("responses", {"truncation": {"private": "alice@example.com"}}),
        ("responses", {"reasoning": {"effort": "alice@example.com"}}),
        ("responses", {"reasoning": {"summary": ["private"]}}),
        ("responses", {"include": ["alice@example.com"]}),
        ("responses", {"thinking": {"private": "alice@example.com"}}),
        (
            "chat_completions",
            {"stream_options": {"include_usage": True, "private": "alice@example.com"}},
        ),
        ("chat_completions", {"stream_options": {"include_usage": "private"}}),
        ("chat_completions", {"instructions": "private"}),
        ("responses", {"messages": []}),
        ("chat_completions", {"stop": {"id": "alice@example.com"}}),
        ("chat_completions", {"stop": [{"type": "alice@example.com"}]}),
    ],
)
def test_api_controls_reject_untyped_private_data(dialect, options):
    field = "messages" if dialect == "chat_completions" else "input"
    with pytest.raises(ValueError):
        GatewaySession().request({field: [], **options}, dialect=dialect)


def test_chat_private_stop_sequences_are_masked():
    session = GatewaySession()
    payload = {
        "model": "fixture",
        "messages": [],
        "stop": ["alice@example.com", "고객조회"],
    }
    masked = session.request(payload, dialect="chat_completions")
    assert "alice@example.com" not in json.dumps(masked, ensure_ascii=False)
    assert "고객조회" not in json.dumps(masked, ensure_ascii=False)
    assert [session.restore(value) for value in masked["stop"]] == payload["stop"]


def test_api_valid_public_controls_remain_usable():
    masked = GatewaySession().request(
        {
            "input": [],
            "stream": False,
            "store": True,
            "parallel_tool_calls": True,
            "max_output_tokens": 16,
            "temperature": 0.5,
            "top_p": 1,
            "reasoning": {"effort": "low", "summary": "concise"},
            "include": ["reasoning.encrypted_content"],
            "service_tier": "default",
        },
        dialect="responses",
    )
    assert masked["store"] is False
    assert masked["max_output_tokens"] == 16
    assert masked["reasoning"] == {"effort": "low", "summary": "concise"}


def test_xai_drops_subscription_headers_and_routes(xai_gateway):
    gateway, provider = xai_gateway
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
        json.dumps({"input": [{"role": "user", "content": "hello"}]}).encode(),
        {
            "X-SecureMCP-Token": "xai-local-token",
            "Authorization": "Bearer xai-fixture",
            "chatgpt-account-id": "private-account",
            "anthropic-beta": "private-beta",
            "OpenAI-Beta": "private-openai",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    headers = {key.lower(): value for key, value in provider.requests[-1][2].items()}
    for header in (
        "chatgpt-account-id",
        "anthropic-beta",
        "openai-beta",
        "x-securemcp-token",
    ):
        assert header not in headers
    for route, auth in [
        ("/claude/v1/messages", "Bearer sk-ant-oat01-fixture"),
        ("/codex/responses", "Bearer eyJfixture"),
    ]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}{route}",
            b"{}",
            {"X-SecureMCP-Token": "xai-local-token", "Authorization": auth},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 400
        error.value.close()
    assert len(provider.requests) == 1


def test_xai_redirects_cannot_forward_credentials(xai_gateway):
    gateway, provider = xai_gateway
    provider.redirect = True
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
        json.dumps({"input": [{"role": "user", "content": "hello"}]}).encode(),
        {"X-SecureMCP-Token": "xai-local-token", "Authorization": "Bearer xai-fixture"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 502
    error.value.close()
    assert len(provider.requests) == 1
    assert provider.requests[0][0] == "/v1/responses"


def test_xai_upstream_ignores_environment_proxy(xai_gateway, monkeypatch):
    gateway, provider = xai_gateway
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.server_port}/xai/v1/responses",
        json.dumps({"input": [{"role": "user", "content": "hello"}]}).encode(),
        {"X-SecureMCP-Token": "xai-local-token", "Authorization": "Bearer xai-fixture"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
    assert len(provider.requests) == 1


@pytest.mark.parametrize("identifier", ["privateFunction", "고객조회"])
def test_chat_sse_split_text_reasoning_and_tool_restore(identifier):
    session = GatewaySession()
    alias = session.mask(identifier, code=True)
    args = json.dumps({"file_path": alias})
    frames = []
    for text, arguments in [(alias[:4], args[:8]), (alias[4:], args[8:])]:
        delta = {
            "content": text,
            "reasoning_content": text,
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": text, "arguments": arguments},
                }
            ],
        }
        frames.append(
            "data: "
            + json.dumps({"id": "chat_1", "choices": [{"index": 0, "delta": delta}]})
            + "\n\n"
        )
    body = ("".join(frames) + "data: [DONE]\n\n").encode()
    restored = session.response(
        body, "text/event-stream", dialect="chat_completions"
    ).decode()
    events = [
        json.loads(frame[6:])
        for frame in restored.split("\n\n")
        if frame.startswith("data: {")
    ]
    deltas = [event["choices"][0]["delta"] for event in events]
    for field in ("content", "reasoning_content"):
        assert "".join(delta.get(field, "") for delta in deltas) == identifier
    functions = [delta["tool_calls"][0]["function"] for delta in deltas]
    assert "".join(function.get("name", "") for function in functions) == identifier
    assert json.loads(
        "".join(function.get("arguments", "") for function in functions)
    ) == {"file_path": identifier}
    assert "data: [DONE]" in restored


@pytest.mark.skipif(
    os.environ.get("SECURE_MCP_LIVE_XAI_API") != "1"
    or not os.environ.get("XAI_API_KEY")
    or not os.environ.get("SECURE_MCP_XAI_MODEL"),
    reason="Opt-in separately billed xAI API smoke requires explicit flag, key, and model",
)
def test_live_xai_api_smoke():
    """SECURE_MCP_LIVE_XAI_API=1 incurs API cost; browser subscriptions do not apply."""
    gateway = PrivacyGateway(
        ("127.0.0.1", 0),
        "live-xai-local-token",
        upstreams={},
        api_upstreams={"xai": "https://api.x.ai"},
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/xai/v1/chat/completions",
            json.dumps(
                {
                    "model": os.environ["SECURE_MCP_XAI_MODEL"],
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 16,
                }
            ).encode(),
            {
                "X-SecureMCP-Token": "live-xai-local-token",
                "Authorization": "Bearer " + os.environ["XAI_API_KEY"],
            },
        )
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.load(response)
        assert isinstance(body["choices"][0]["message"]["content"], str)
    finally:
        gateway.shutdown()
        gateway.server_close()
