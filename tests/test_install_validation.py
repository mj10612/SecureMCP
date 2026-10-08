"""Absent installs, invalid restore manifests, and interrupted shutdowns."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hashlib import sha256
import json
from threading import Thread

import pytest

from secure_mcp import gateway_install
from secure_mcp.xai_install import install_api


def test_absent_stop_and_uninstall_are_idempotent(tmp_path):
    config = tmp_path / "not created" / "config.json"
    assert gateway_install.stop_gateway(config) is False
    assert gateway_install.uninstall_gateway(config) is False
    assert not config.parent.exists()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"port": 38117, "token": "t" * 32}))
    assert gateway_install.uninstall_gateway(config) is False
    assert gateway_install.uninstall_gateway(config) is False


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        [],
        {},
        {"oops": 1},
        {"files": None},
        {"files": []},
        {"files": {"relative": {"original_hex": None, "installed_sha256": "a" * 64}}},
        {"files": {}, "uninstall_started": "true"},
        "record-not-object",
        "missing-original",
        "missing-digest",
        "bad-original",
        "odd-original",
        "bad-digest",
        "nonstring-original",
    ],
)
@pytest.mark.parametrize(
    "operation", ["subscription-install", "api-install", "uninstall", "apply-settings"]
)
def test_invalid_manifest_fails_before_mutation(tmp_path, manifest, operation):
    settings = tmp_path / "host" / "settings.json"
    settings.parent.mkdir()
    settings.write_bytes(b'{"personal":true}')
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "port": 38117,
                "token": "t" * 32,
                **(
                    {"mode": "api", "provider": "xai"}
                    if operation == "api-install"
                    else {}
                ),
            }
        )
    )
    record = {
        "original_hex": "00",
        "installed_sha256": sha256(settings.read_bytes()).hexdigest(),
    }
    if isinstance(manifest, str):
        mutations = {
            "record-not-object": [],
            "missing-original": {"installed_sha256": "a" * 64},
            "missing-digest": {"original_hex": None},
            "bad-original": {**record, "original_hex": "zz"},
            "odd-original": {**record, "original_hex": "a"},
            "bad-digest": {**record, "installed_sha256": "not-a-hash"},
            "nonstring-original": {**record, "original_hex": []},
        }
        manifest = {"files": {str(settings): mutations[manifest]}}
    manifest_path = tmp_path / "installation.json"
    manifest_path.write_text(json.dumps(manifest))
    before = {path: path.read_bytes() for path in (config, settings, manifest_path)}
    with pytest.raises(
        ValueError, match="Invalid installation manifest.*restore/merge"
    ):
        if operation == "subscription-install":
            gateway_install.install_gateway(
                config,
                agent="claude",
                claude_dir=settings.parent,
                startup_path=tmp_path / "startup",
            )
        elif operation == "api-install":
            install_api(config, "grok-fixture", port=38117, grok_dir=tmp_path / "grok")
        elif operation == "uninstall":
            gateway_install.uninstall_gateway(config)
        else:
            gateway_install.apply_settings(
                config, json.loads(config.read_text()), {settings: b"new-settings"}
            )
    assert {path: path.read_bytes() for path in before} == before
    assert not (tmp_path / "startup").exists()
    assert not (tmp_path / "grok").exists()
    assert not config.with_name(config.name + ".lock").exists()


def test_invalid_json_manifest_has_recovery_message(tmp_path):
    manifest = tmp_path / "installation.json"
    manifest.write_bytes(b'{"files":')
    with pytest.raises(ValueError, match="Invalid installation manifest"):
        gateway_install.load_installation_manifest(manifest)


@pytest.mark.parametrize(
    "shutdown_kind, stops",
    [("disconnect", True), ("truncated", True), ("disconnect", False)],
)
def test_stop_confirms_shutdown_after_interrupted_response(
    tmp_path, shutdown_kind, stops
):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"service":"secure-mcp-subscription-gateway"}')

        def do_POST(self):
            if shutdown_kind == "truncated":
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b'{"stopping":')
            if stops:
                Thread(target=self.server.shutdown, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"port": server.server_port, "token": "t" * 32}))
    try:
        if stops:
            assert gateway_install.stop_gateway(config) is True
        else:
            with pytest.raises(OSError, match="remained active"):
                gateway_install.stop_gateway(config)
            assert gateway_install.healthy(json.loads(config.read_text()))
    finally:
        server.shutdown()
        server.server_close()


def test_shutdown_auth_failure_is_not_treated_as_success(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"service":"secure-mcp-subscription-gateway"}')

        def do_POST(self):
            self.send_response(401)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"port": server.server_port, "token": "t" * 32}))
    try:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as error:
            gateway_install.stop_gateway(config)
        assert error.value.code == 401
        error.value.close()
        assert gateway_install.healthy(json.loads(config.read_text()))
    finally:
        server.shutdown()
        server.server_close()
