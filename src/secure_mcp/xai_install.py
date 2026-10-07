"""Explicit xAI API setup; never changes browser login or subscription settings."""

import os
import json
from pathlib import Path
import secrets

import tomlkit

from secure_mcp.encrypted_session import session_file_lock
from secure_mcp.gateway import validate_api_options
from secure_mcp.gateway_install import apply_settings, load_config


def default_api_config():
    return Path.home() / ".secure-mcp" / "xai-api" / "config.json"


def load_api_config(path):
    config = load_config(path)
    if config.get("mode") != "api" or config.get("provider") != "xai":
        raise ValueError("Expected an isolated xAI API configuration.")
    return config


def install_api(path: Path, model: str, port=38118, grok_dir=None):
    if not model.strip() or any(ord(character) < 32 for character in model):
        raise ValueError("A nonempty xAI model ID is required.")
    validate_api_options({"model": model}, "responses")
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid API gateway port.")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with session_file_lock(path):
        config = (
            load_api_config(path)
            if path.exists()
            else {
                "port": port,
                "token": secrets.token_urlsafe(32),
                "mode": "api",
                "provider": "xai",
            }
        )
        manifest = path.parent / "installation.json"
        if manifest.exists() and config["port"] != port:
            raise ValueError("Uninstall API mode before changing its port.")
        config["port"] = port
        settings = (
            grok_dir or Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
        ) / "config.toml"
        value = (
            tomlkit.parse(settings.read_text(encoding="utf-8"))
            if settings.exists()
            else tomlkit.document()
        )
        models = value.setdefault("model", {})
        records = (
            json.loads(manifest.read_text(encoding="utf-8"))["files"]
            if manifest.exists()
            else {}
        )
        if "secure_mcp_xai" in models and str(settings.resolve()) not in records:
            raise ValueError(
                "The secure_mcp_xai model already exists; choose a clean configuration."
            )
        models["secure_mcp_xai"] = {
            "name": "SecureMCP xAI API (separate API billing)",
            "model": model,
            "base_url": f"http://127.0.0.1:{port}/xai/v1",
            "api_backend": "responses",
            "env_key": "XAI_API_KEY",
            "extra_headers": {"X-SecureMCP-Token": config["token"]},
            "supports_backend_search": False,
        }
        # Add an explicitly selectable model; do not change the user's default.
        return apply_settings(path, config, {settings: tomlkit.dumps(value).encode()})
