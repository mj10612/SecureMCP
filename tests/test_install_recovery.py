"""Installation recovery, shared locking, and malformed health response proof."""

from hashlib import sha256
import json
from pathlib import Path
import socketserver
import subprocess
import sys
from threading import Thread
import time

import pytest

from secure_mcp import gateway_install


@pytest.fixture
def installed(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"port": 38117, "token": "t" * 32}))
    originals = [b"personal-first", None, b"personal-last"]
    paths = [tmp_path / f"settings-{index}" for index in range(3)]
    records = {}
    for index, (path, original) in enumerate(zip(paths, originals)):
        path.write_bytes(f"installed-{index}".encode())
        records[str(path)] = {
            "original_hex": original.hex() if original is not None else None,
            "installed_sha256": sha256(path.read_bytes()).hexdigest(),
        }
    manifest = tmp_path / "installation.json"
    manifest.write_text(json.dumps({"files": records}))
    monkeypatch.setattr(gateway_install, "stop_gateway", lambda _: None)
    return config, paths, originals, manifest


@pytest.mark.parametrize("failure", [0, 1, 2])
def test_uninstall_failure_can_retry_each_restore_step(installed, monkeypatch, failure):
    config, paths, originals, manifest = installed
    write = gateway_install.atomic_bytes
    unlink = Path.unlink

    def fail_write(path, data):
        if path == paths[failure]:
            raise OSError("simulated restore failure")
        return write(path, data)

    def fail_unlink(path, *args, **kwargs):
        if path == paths[failure]:
            raise OSError("simulated restore failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(gateway_install, "atomic_bytes", fail_write)
        failure_patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="restore failure"):
            gateway_install.uninstall_gateway(config)
    assert json.loads(manifest.read_text())["uninstall_started"] is True
    gateway_install.uninstall_gateway(config)
    for path, original in zip(paths, originals):
        assert (
            path.read_bytes() == original if original is not None else not path.exists()
        )
    assert not manifest.exists()


def test_manifest_removal_failure_can_retry_completed_restore(installed, monkeypatch):
    config, _, _, manifest = installed
    unlink = Path.unlink

    def fail_manifest(path, *args, **kwargs):
        if path == manifest:
            raise OSError("manifest deletion failed")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(Path, "unlink", fail_manifest)
        with pytest.raises(OSError, match="manifest deletion"):
            gateway_install.uninstall_gateway(config)
    gateway_install.uninstall_gateway(config)
    assert not manifest.exists()


def test_recovery_preserves_new_user_edits_and_blocks_install(installed, monkeypatch):
    config, paths, _, manifest = installed
    write = gateway_install.atomic_bytes

    def fail_last(path, data):
        if path == paths[-1]:
            raise OSError("restore failure")
        return write(path, data)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(gateway_install, "atomic_bytes", fail_last)
        with pytest.raises(OSError):
            gateway_install.uninstall_gateway(config)
    paths[0].write_bytes(b"new user edits")
    with pytest.raises(ValueError, match="settings changed"):
        gateway_install.uninstall_gateway(config)
    assert paths[0].read_bytes() == b"new user edits"
    assert manifest.exists()
    with pytest.raises(ValueError, match="recovery is incomplete"):
        gateway_install.apply_settings(config, json.loads(config.read_text()), {})


def test_uninstall_rejects_install_in_another_process_then_retries(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.json"
    home = tmp_path / "host"
    home.mkdir()
    settings = home / "settings.json"
    original = b'{"personal":true}\n'
    settings.write_bytes(original)
    ready, release = tmp_path / "ready", tmp_path / "release"
    script = """
from pathlib import Path
import sys, time
from secure_mcp import gateway_install as install
config, home, startup, ready, release = map(Path, sys.argv[1:])
write = install.atomic_bytes
def paused_write(path, data):
    if path == home / 'settings.json':
        ready.write_text('ready')
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test release timeout')
            time.sleep(0.02)
    return write(path, data)
install.atomic_bytes = paused_write
install.install_gateway(config, agent='claude', claude_dir=home, startup_path=startup)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(config),
            str(home),
            str(tmp_path / "startup"),
            str(ready),
            str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    monkeypatch.setattr(gateway_install, "stop_gateway", lambda _: None)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"installer did not reach barrier: {stdout!r} {stderr!r}")
            time.sleep(0.02)
        manifest = (tmp_path / "installation.json").read_bytes()
        with pytest.raises(ValueError, match="in use"):
            gateway_install.uninstall_gateway(config)
        assert settings.read_bytes() == original
        assert (tmp_path / "installation.json").read_bytes() == manifest
        release.write_text("release")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        gateway_install.uninstall_gateway(config)
        assert settings.read_bytes() == original
        assert not (tmp_path / "installation.json").exists()
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize(
    "wire",
    [
        b'HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{"service":',
        b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nxxx",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]",
        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nnull",
        b"not-an-http-response\r\n\r\n",
        b"",
    ],
)
def test_broken_health_response_is_not_healthy(wire):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(wire)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert not gateway_install.healthy(
            {"port": server.server_address[1], "token": "fixture"}
        )
    finally:
        server.shutdown()
        server.server_close()
