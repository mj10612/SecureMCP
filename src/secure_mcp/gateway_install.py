"""One-time host setup and automatic loopback startup; never changes login caches."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import plistlib
import subprocess
import sys
import time
import tempfile
from threading import Thread
import urllib.error
import urllib.request

import tomlkit

from secure_mcp.encrypted_session import session_file_lock
from secure_mcp.hooks import _atomic_json, _remove_owned


def default_config() -> Path:
    return Path.home() / ".secure-mcp" / "gateway" / "config.json"


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".secure-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(path: Path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Gateway configuration must be a JSON object.")
    if not isinstance(config.get("port"), int) or not 1024 <= config["port"] <= 65535:
        raise ValueError("Invalid gateway port.")
    if not isinstance(config.get("token"), str) or len(config["token"]) < 32:
        raise ValueError("Invalid local gateway token.")
    return config


def healthy(config):
    request = urllib.request.Request(
        f"http://127.0.0.1:{config['port']}/health",
        headers={"X-SecureMCP-Token": config["token"]},
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=1
        ) as response:
            return (
                json.load(response).get("service") == "secure-mcp-subscription-gateway"
            )
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except (OSError, ValueError):
        return False


def ensure_gateway(path: Path):
    config = load_config(path)
    with session_file_lock(path):
        if healthy(config):
            return
        options = (
            {
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            }
            if os.name == "nt"
            else {"start_new_session": True}
        )
        # Keep the child's stderr for diagnostics only; prompts and tokens are
        # never logged by the gateway. A temp file avoids pipe backpressure.
        diagnostics = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "secure_mcp",
                    "gateway",
                    "run",
                    "--config",
                    str(path.resolve()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostics,
                **options,
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if healthy(config):
                    # Keep the child handle owned and reap it after shutdown.
                    Thread(target=process.wait, daemon=True).start()
                    return
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            diagnostics.seek(0)
            detail = diagnostics.read(2000).decode("utf-8", "replace").strip()
            message = "Local gateway could not start; no direct-provider fallback was enabled."
            if detail:
                message += " (" + detail[-1000:] + ")"
            raise ValueError(message)
        finally:
            diagnostics.close()


def stop_gateway(path: Path):
    config = load_config(path)
    if not healthy(config):
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{config['port']}/shutdown",
        data=b"",
        headers={"X-SecureMCP-Token": config["token"]},
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        request, timeout=5
    ):
        pass


def install_gateway(
    path: Path,
    agent="both",
    port=38117,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    startup_path: Path | None = None,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    with session_file_lock(path):
        return _install_gateway(path, agent, port, claude_dir, codex_dir, startup_path)


def startup_registration(arguments, startup_path=None):
    """Use user-login startup, without installing agent hooks or a system service."""
    if any("\n" in argument or "\r" in argument for argument in arguments):
        raise ValueError("Startup arguments must not contain line breaks.")
    if sys.platform == "win32":
        destination = startup_path or (
            Path(os.environ["APPDATA"])
            / "Microsoft/Windows/Start Menu/Programs/Startup/SecureMCP.vbs"
        )
        command = subprocess.list2cmdline(arguments).replace('"', '""')
        data = f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n'.encode(
            "utf-16"
        )
    elif sys.platform == "darwin":
        destination = (
            startup_path
            or Path.home() / "Library/LaunchAgents/io.securemcp.gateway.plist"
        )
        data = plistlib.dumps(
            {
                "Label": "io.securemcp.gateway",
                "RunAtLoad": True,
                "ProgramArguments": arguments,
            }
        )
    else:
        destination = (
            startup_path
            or Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "autostart/secure-mcp.desktop"
        )

        def quote(value):
            return (
                '"'
                + value.replace("%", "%%")
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("`", "\\`")
                .replace("$", "\\$")
                + '"'
            )

        # Desktop Entry string escaping is decoded before Exec argument quoting.
        command = " ".join(quote(value) for value in arguments).replace("\\", "\\\\")
        data = f"[Desktop Entry]\nType=Application\nName=SecureMCP\nExec={command}\nTerminal=false\n".encode()
    return destination, data


def _install_gateway(path: Path, agent, port, claude_dir, codex_dir, startup_path):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    manifest_path = path.parent / "installation.json"
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    config = (
        load_config(path)
        if path.exists()
        else {"port": port, "token": secrets.token_urlsafe(32)}
    )
    if config.get("mode", "subscription") != "subscription":
        raise ValueError("API configuration cannot be used for subscription setup.")
    if path.exists() and config["port"] != port:
        # After `gateway uninstall` the config file is intentionally retained
        # for recovery (token/state), while installation.json is removed. With
        # no active installation the stored port can be updated instead of
        # leaving users stuck with no supported way to change it.
        if previous_manifest is None:
            config["port"] = port
        else:
            raise ValueError(
                "Existing installation uses another port; uninstall before changing it."
            )
    arguments = [
        sys.executable,
        "-m",
        "secure_mcp",
        "gateway",
        "ensure",
        "--config",
        str(path),
    ]
    startup, startup_data = startup_registration(arguments, startup_path)
    changes = {startup: startup_data}
    if agent in {"claude", "both"}:
        settings = (
            claude_dir
            or Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        ) / "settings.json"
        value = (
            json.loads(settings.read_text(encoding="utf-8"))
            if settings.exists()
            else {}
        )
        env = value.setdefault("env", {})
        if (
            env.get("ANTHROPIC_AUTH_TOKEN")
            or env.get("ANTHROPIC_API_KEY")
            or value.get("apiKeyHelper")
        ):
            raise ValueError(
                "Remove API/gateway credential overrides before installing subscription mode."
            )
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{config['port']}/claude"
        existing = env.get("ANTHROPIC_CUSTOM_HEADERS", "")
        lines = [
            line
            for line in existing.splitlines()
            if not line.lower().startswith("x-securemcp-token:")
        ]
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(
            lines + ["X-SecureMCP-Token: " + config["token"]]
        )
        _remove_owned(value, "claude")
        changes[settings] = (
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        ).encode()
    if agent in {"codex", "both"}:
        home = codex_dir or Path(
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        )
        settings = home / "config.toml"
        value = (
            tomlkit.parse(settings.read_text(encoding="utf-8"))
            if settings.exists()
            else tomlkit.document()
        )
        value["model_provider"] = "secure_mcp_subscription"
        value["forced_login_method"] = "chatgpt"
        providers = value.setdefault("model_providers", {})
        providers["secure_mcp_subscription"] = {
            "name": "SecureMCP subscription",
            "base_url": f"http://127.0.0.1:{config['port']}/codex",
            "wire_api": "responses",
            "requires_openai_auth": True,
            "supports_websockets": False,
            "http_headers": {"X-SecureMCP-Token": config["token"]},
        }
        changes[settings] = tomlkit.dumps(value).encode()
    return apply_settings(path, config, changes)


def apply_settings(path: Path, config, changes):
    """Back up and transactionally apply settings, shared by subscription/API setup."""
    manifest_path = path.parent / "installation.json"
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    manifest = json.loads(previous_manifest) if previous_manifest else {"files": {}}
    # Prepare and validate every file before any host settings are changed.
    for settings, data in changes.items():
        name = str(settings.resolve())
        record = manifest["files"].get(name)
        if (
            record
            and settings.exists()
            and sha256(settings.read_bytes()).hexdigest() != record["installed_sha256"]
        ):
            raise ValueError(
                "Host settings changed after setup; uninstall/merge them before reinstalling."
            )
        if record is None:
            record = {
                "original_hex": settings.read_bytes().hex()
                if settings.exists()
                else None
            }
            manifest["files"][name] = record
        record["installed_sha256"] = sha256(data).hexdigest()
    _atomic_json(path, config)
    path.chmod(0o600)
    _atomic_json(manifest_path, manifest)
    manifest_path.chmod(0o600)
    originals = {
        settings: settings.read_bytes() if settings.exists() else None
        for settings in changes
    }
    applied = []
    try:
        for settings, data in changes.items():
            atomic_bytes(settings, data)
            applied.append(settings)
            settings.chmod(0o600)
    except OSError:
        for settings in reversed(applied):
            original = originals[settings]
            if original is None:
                settings.unlink()
            else:
                atomic_bytes(settings, original)
        if previous_manifest is None:
            manifest_path.unlink()
        else:
            atomic_bytes(manifest_path, previous_manifest)
        raise
    return list(changes)


def uninstall_gateway(path: Path):
    manifest_path = path.parent / "installation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, record in manifest["files"].items():
        settings = Path(name)
        if (
            not settings.is_file()
            or sha256(settings.read_bytes()).hexdigest() != record["installed_sha256"]
        ):
            raise ValueError(
                "Host settings changed; refusing to overwrite them. Restore/merge installation.json manually."
            )
    stop_gateway(path)
    for name, record in manifest["files"].items():
        settings = Path(name)
        if record["original_hex"] is None:
            settings.unlink()
        else:
            atomic_bytes(settings, bytes.fromhex(record["original_hex"]))
    manifest_path.unlink()
