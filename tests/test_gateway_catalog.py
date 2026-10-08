"""xAI catalog trust boundaries; all network traffic remains on loopback."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import urllib.error
import urllib.request

import pytest

from secure_mcp.gateway import PrivacyGateway
from secure_mcp.gateway_catalog import fetch_catalog


@pytest.fixture
def catalog_provider():
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.server.requests.append((self.path, dict(self.headers)))
            self.send_response(self.server.status)
            if self.server.status == 307:
                self.send_header("Location", "/private-redirect")
            self.send_header("Content-Type", self.server.content_type)
            self.end_headers()
            self.wfile.write(self.server.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.body = json.dumps({"object": "list", "data": [{"id": "grok-fixture", "created": 1, "owned_by": "xai", "description": "alice@example.com", "metadata": {"secret": "private"}}]}).encode()
    server.content_type = "application/json"
    server.status = 200
    server.requests = []
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_catalog_fixed_route_auth_and_public_metadata(catalog_provider, monkeypatch):
    server, origin = catalog_provider
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    catalog = json.loads(fetch_catalog(origin, "Bearer xai-fixture"))
    assert catalog == {"object": "list", "data": [{"id": "grok-fixture", "object": "model", "created": 1, "owned_by": "xai"}]}
    assert server.requests[0][0] == "/v1/models"
    assert server.requests[0][1]["Authorization"] == "Bearer xai-fixture"
    assert "alice" not in json.dumps(catalog)


@pytest.mark.parametrize("body", [b'[]', b'{"data":null}', b'{"data":[{"id":"alice@example.com"}]}', b'{"data":[{"id":"private\\nheader"}]}', b'{"data":[{"id":42}]}'])
def test_catalog_invalid_shape_or_ids_fail_closed(catalog_provider, body):
    server, origin = catalog_provider
    server.body = body
    with pytest.raises(ValueError):
        fetch_catalog(origin, "Bearer xai-fixture")


def test_catalog_arbitrary_owner_and_metadata_types_are_dropped(catalog_provider):
    server, origin = catalog_provider
    server.body = b'{"data":[{"id":"grok-fixture","owned_by":["secret"],"created":true}]}'
    assert json.loads(fetch_catalog(origin, "Bearer xai-fixture"))["data"] == [{"id": "grok-fixture", "object": "model"}]


def test_catalog_size_and_redirect_bounds(catalog_provider, monkeypatch):
    import secure_mcp.gateway as gateway

    server, origin = catalog_provider
    monkeypatch.setattr(gateway, "LIMIT", 8)
    with pytest.raises(ValueError, match="size"):
        fetch_catalog(origin, "Bearer xai-fixture")
    server.status = 307
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch_catalog(origin, "Bearer xai-fixture")
    assert error.value.code == 307
    error.value.close()
    assert len(server.requests) == 2
    assert all(path == "/v1/models" for path, _ in server.requests)


@pytest.mark.parametrize("origin,auth", [("https://example.com", "Bearer xai-fixture"), ("http://127.0.0.1:1/private", "Bearer xai-fixture"), ("http://127.0.0.1:1", "Bearer eyJ-subscription"), ("http://127.0.0.1:1", "Bearer xai-fixture\nsecret")])
def test_catalog_refuses_arbitrary_origins_and_nonapi_credentials(origin, auth):
    with pytest.raises(ValueError):
        fetch_catalog(origin, auth)


def test_xai_models_gateway_requires_both_tokens(catalog_provider):
    provider, origin = catalog_provider
    gateway = PrivacyGateway(("127.0.0.1", 0), "local-secret", upstreams={}, api_upstreams={"xai": origin})
    worker = Thread(target=gateway.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{gateway.server_port}/xai/v1/models"
        for headers in [{}, {"X-SecureMCP-Token": "local-secret"}, {"X-SecureMCP-Token": "wrong", "Authorization": "Bearer xai-fixture"}, {"X-SecureMCP-Token": "local-secret", "Authorization": "Bearer eyJ-subscription"}]:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5)
            assert error.value.code == 401
            error.value.close()
        assert provider.requests == []
        request = urllib.request.Request(url, headers={"X-SecureMCP-Token": "local-secret", "Authorization": "Bearer xai-fixture"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.load(response)["data"][0]["id"] == "grok-fixture"
        assert len(provider.requests) == 1
        assert "x-securemcp-token" not in {key.lower() for key in provider.requests[0][1]}
    finally:
        gateway.shutdown()
        gateway.server_close()
        worker.join()
