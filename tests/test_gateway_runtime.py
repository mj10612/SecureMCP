"""Concurrency, recovery, and safe public upstream errors."""

from io import BytesIO
import json
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
from urllib.error import HTTPError

import pytest

from secure_mcp.gateway import PrivacyGateway, sanitized_upstream_error


@pytest.mark.parametrize(
    "status", [400, 401, 403, 404, 413, 422, 429, 500, 503, 504, 529]
)
def test_public_upstream_status_and_code_without_private_body(status):
    exc = HTTPError(
        "https://provider/private",
        status,
        "private",
        {},
        BytesIO(
            json.dumps(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                        "message": "alice@example.com",
                        "param": "private",
                    }
                }
            ).encode()
        ),
    )
    actual, body = sanitized_upstream_error(exc)
    assert actual == status
    assert json.loads(body)["error"]["code"] == "context_length_exceeded"
    assert b"alice" not in body and b"private" not in body


def test_unknown_error_fields_and_redirect_are_sanitized():
    exc = HTTPError(
        "https://provider",
        302,
        "private",
        {},
        BytesIO(b'{"error":{"type":"private","code":"private"}}'),
    )
    status, body = sanitized_upstream_error(exc)
    assert status == 502
    assert set(json.loads(body)["error"]) == {"message"}


def test_mapping_limit_reuse_and_authenticated_reset(tmp_path):
    with PrivacyGateway(
        ("127.0.0.1", 0),
        "secret",
        upstreams={"claude": "unused"},
        state_dir=tmp_path,
        mapping_limit=1,
    ) as server:
        session = server.get_session("claude")
        alias = session.mask("privateAlpha", code=True)
        assert session.mask("privateAlpha", code=True) == alias
        with pytest.raises(ValueError, match="Mapping limit"):
            session.mask("privateBeta", code=True)
        server.save("claude", session)
        session.active_requests = 1
        with pytest.raises(ValueError, match="active"):
            server.reset("claude")
        session.active_requests = 0
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}/reset"
        request = urllib.request.Request(
            url, data=b'{"agent":"claude"}', headers={"X-SecureMCP-Token": "wrong"}
        )
        with pytest.raises(HTTPError) as caught:
            urllib.request.urlopen(request)
        assert caught.value.code == 401 and server.get_session("claude") is session
        caught.value.close()
        request.add_header("X-SecureMCP-Token", "secret")
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
        assert session.session.generator.closed
        assert not (tmp_path / "claude-symbols-v2.enc").exists()
        assert server.get_session("claude").mask("privateBeta", code=True)
        server.shutdown()
        worker.join(2)


def test_gateway_does_not_hold_alias_lock_while_provider_waits():
    entered, release, completed = Event(), Event(), Event()
    paths = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            paths.append(self.path)
            if self.path.split("?", 1)[0].endswith("messages"):
                entered.set()
                assert release.wait(5)
            body = b'{"content":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as upstream:
        thread = Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{upstream.server_port}"
        with PrivacyGateway(
            ("127.0.0.1", 0), "secret", upstreams={"claude": origin, "codex": origin}
        ) as gateway:
            worker = Thread(target=gateway.serve_forever, daemon=True)
            worker.start()
            errors = []

            def call(path):
                payload = (
                    {"messages": []} if path.split("?", 1)[0].endswith("messages") else {"input": []}
                )
                request = urllib.request.Request(
                    f"http://127.0.0.1:{gateway.server_port}" + path,
                    data=json.dumps(payload).encode(),
                    headers={
                        "X-SecureMCP-Token": "secret",
                        "Authorization": "Bearer sk-ant-oat-test"
                        if "claude" in path
                        else "Bearer eyJ-test",
                    },
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        response.read()
                except Exception as exc:
                    errors.append(exc)

            first = Thread(target=call, args=("/claude/v1/messages?beta=true&client_version=1.2",))
            first.start()
            assert entered.wait(3)
            session = gateway.get_session("claude")
            assert session.lock.acquire(blocking=False)
            session.lock.release()

            def other():
                call("/codex/responses")
                completed.set()

            second = Thread(target=other)
            second.start()
            try:
                assert completed.wait(2), (
                    "independent provider blocked by first upstream"
                )
            finally:
                release.set()
                first.join(5)
                second.join(5)
                gateway.shutdown()
                worker.join(2)
            assert not errors
            assert "/v1/messages?beta=true&client_version=1.2" in paths
        upstream.shutdown()
        thread.join(2)


@pytest.mark.parametrize(
    "event",
    [
        {"choices": {}},
        {"choices": [{}]},
        {"choices": [{"index": 0, "delta": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [None]}}]},
        {
            "choices": [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": None}]}}
            ]
        },
    ],
)
def test_malformed_chat_stream_rejects_with_value_error(event):
    from secure_mcp.gateway import GatewaySession

    with pytest.raises(ValueError):
        GatewaySession().response(
            ("data: " + json.dumps(event) + "\n\n").encode(),
            "text/event-stream",
            dialect="chat_completions",
        )


@pytest.mark.parametrize("query", ["beta=true", "beta=false&client_version=1.2.3", "api-version=2026-01-01"])
def test_supported_query_preserves_public_metadata(query):
    from secure_mcp.gateway import supported_query
    assert supported_query(query)


@pytest.mark.parametrize("query", ["private=alice@example.com", "beta=private", "client_version=alice", "beta=true&beta=false"])
def test_private_or_ambiguous_query_is_rejected(query):
    from secure_mcp.gateway import supported_query
    assert not supported_query(query)


def test_gateway_snapshot_derives_key_once_per_owner_and_drops_on_reset(tmp_path, monkeypatch):
    import secure_mcp.encrypted_session as storage
    count = []
    original = storage._cipher
    def counted(password, salt):
        count.append(salt)
        return original(password, salt)
    monkeypatch.setattr(storage, "_cipher", counted)
    with PrivacyGateway(("127.0.0.1",0),"secret",upstreams={"claude":"unused"},state_dir=tmp_path) as gateway:
        session = gateway.get_session("claude")
        session.mask("privateAlpha", code=True)
        gateway.save("claude",session)
        first = (tmp_path / "claude-symbols-v2.enc").read_bytes()
        gateway.save("claude",session)
        assert len(count) == 1
        assert first != (tmp_path / "claude-symbols-v2.enc").read_bytes()
        gateway.reset("claude")
        assert session.encryption is None
        new = gateway.get_session("claude")
        gateway.save("claude",new)
        assert len(count) == 2 and count[0] != count[1]


@pytest.mark.parametrize("data", ["null", "[]", "123", '\"private\"'])
@pytest.mark.parametrize("dialect", [None, "responses", "chat_completions"])
def test_scalar_sse_events_fail_closed(data, dialect):
    from secure_mcp.gateway import GatewaySession
    with pytest.raises(ValueError):
        GatewaySession().response(("data: "+data+"\n\n").encode(), "text/event-stream", dialect=dialect)
