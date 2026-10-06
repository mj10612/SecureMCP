"""Offline contract for the explicit xAI API mode (issue #66).

These tests never contact api.x.ai. They verify that API-key mode is opt-in,
isolated from subscription auth, and uses the same masking/restoration path.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import urllib.error
import urllib.request

import pytest

from secure_mcp.gateway import PrivacyGateway


class XaiProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload, dict(self.headers)))
        text = json.dumps(payload.get("input", payload.get("messages", [])), ensure_ascii=False)
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
