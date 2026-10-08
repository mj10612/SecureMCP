"""Authenticated, context-owned transport for the headerless Antigravity SDK.

Only an explicit xAI API gateway is supported. The SDK receives a short-lived
bearer capability URL, never the provider API key or the gateway header token.
"""

from http.client import HTTPException
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

from secure_mcp.gateway import LIMIT, NoRedirect


@contextmanager
def antigravity_endpoint(gateway_origin: str, local_token: str, api_key: str):
    """Yield an SDK ``base_url`` for the lifetime of this context only.

    ``gateway_origin`` must be an explicit IPv4 loopback HTTP origin. The
    authenticated gateway owns masking, session state and the fixed provider
    origin. This transport never contacts a provider directly.
    """
    try:
        origin = urllib.parse.urlsplit(gateway_origin)
        valid_origin = (
            origin.scheme == "http"
            and origin.hostname == "127.0.0.1"
            and origin.port is not None
            and 1 <= origin.port <= 65535
            and origin.path in {"", "/"}
            and not origin.query
            and not origin.fragment
            and origin.username is None
            and origin.password is None
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise ValueError(
            "SDK transport requires an explicit IPv4 loopback gateway origin."
        )
    if (
        not isinstance(local_token, str)
        or not 32 <= len(local_token) <= 4096
        or not isinstance(api_key, str)
        or not api_key.startswith("xai-")
        or not 5 <= len(api_key) <= 4096
        or any(ord(char) < 33 or ord(char) > 126 for char in local_token + api_key)
    ):
        raise ValueError(
            "SDK transport requires a local token and an explicit xAI API key."
        )
    capability = secrets.token_urlsafe(32)
    endpoint = f"http://127.0.0.1:{origin.port}/xai/v1/chat/completions"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def error(self, status):
            self.respond(
                status, b'{"error":"SDK gateway request refused"}', "application/json"
            )

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_request(self):
            # Authenticate before reading payloads. Never reflect capability URLs.
            parts = self.path.split("/")
            supplied = parts[1] if len(parts) > 1 else ""
            if not secrets.compare_digest(supplied.encode(), capability.encode()):
                self.error(401)
                return
            if (
                self.command != "POST"
                or self.path != f"/{capability}/v1/chat/completions"
            ):
                self.error(400)
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if (
                    len(lengths) != 1
                    or self.headers.get("Transfer-Encoding")
                    or self.headers.get("Content-Encoding")
                    or self.headers.get_content_type() != "application/json"
                ):
                    self.error(400)
                    return
                length = int(lengths[0])
                if not 0 < length <= LIMIT:
                    self.error(413)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    self.error(400)
                    return
                request = urllib.request.Request(
                    endpoint,
                    body,
                    {
                        "Content-Type": "application/json",
                        "X-SecureMCP-Token": local_token,
                        "Authorization": "Bearer " + api_key,
                    },
                )
                with opener.open(request, timeout=65) as response:
                    result = response.read(LIMIT + 1)
                    content_type = response.headers.get_content_type()
                    if len(result) > LIMIT or content_type not in {
                        "application/json",
                        "text/event-stream",
                    }:
                        self.error(502)
                        return
                    self.respond(response.status, result, content_type)
            except urllib.error.HTTPError as exc:
                status = exc.code if 400 <= exc.code <= 599 else 502
                exc.close()
                self.error(status)
            except (OSError, ValueError, urllib.error.URLError, HTTPException):
                self.error(502)

        do_POST = handle_request
        do_GET = handle_request
        do_PUT = handle_request
        do_DELETE = handle_request
        do_PATCH = handle_request
        do_HEAD = handle_request
        do_OPTIONS = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Close sockets and wait for owned requests on context exit; no detached worker.
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/{capability}/v1"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
