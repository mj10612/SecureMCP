"""Offline xAI setup, preservation, and rollback contract."""

import json

import pytest
import tomlkit
from click.testing import CliRunner

from secure_mcp.cli import main
from secure_mcp.gateway_install import uninstall_gateway
from secure_mcp.xai_install import install_api, load_api_config


@pytest.fixture
def api_paths(tmp_path):
    home = tmp_path / "Grok home with spaces"
    home.mkdir()
    config = tmp_path / "API state with spaces" / "config.json"
    return config, home, home / "config.toml"


def test_install_preserves_settings_and_login_then_restores_bytes(api_paths):
    config, home, settings = api_paths
    original = b'# personal settings\ndefault_model = "existing"\n[model.existing]\nmodel = "personal"\n[hooks]\nfinish = "echo done"\n[mcp_servers.local]\ncommand = "local"\n'
    settings.write_bytes(original)
    login = home / "auth.json"
    login.write_bytes(b'{"token":"private-login"}')
    install_api(config, "grok-fixture", grok_dir=home)
    value = tomlkit.parse(settings.read_text(encoding="utf-8"))
    assert value["default_model"] == "existing"
    assert value["model"]["existing"]["model"] == "personal"
    assert value["hooks"]["finish"] == "echo done"
    assert value["mcp_servers"]["local"]["command"] == "local"
    model = value["model"]["secure_mcp_xai"]
    assert model["model"] == "grok-fixture"
    assert model["env_key"] == "XAI_API_KEY"
    assert model["supports_backend_search"] is False
    assert (
        model["extra_headers"]["X-SecureMCP-Token"] == load_api_config(config)["token"]
    )
    assert login.read_bytes() == b'{"token":"private-login"}'
    uninstall_gateway(config)
    assert settings.read_bytes() == original
    assert login.read_bytes() == b'{"token":"private-login"}'


def test_uninstall_removes_new_settings_and_reinstall_preserves_token(api_paths):
    config, home, settings = api_paths
    install_api(config, "grok-one", grok_dir=home)
    token = load_api_config(config)["token"]
    install_api(config, "grok-two", grok_dir=home)
    assert load_api_config(config)["token"] == token
    assert (
        tomlkit.parse(settings.read_text())["model"]["secure_mcp_xai"]["model"]
        == "grok-two"
    )
    uninstall_gateway(config)
    assert not settings.exists()


@pytest.mark.parametrize(
    "original", [b"broken = [", b'[model.secure_mcp_xai]\nmodel = "personal"\n']
)
def test_malformed_or_collision_leaves_original_untouched(api_paths, original):
    config, home, settings = api_paths
    settings.write_bytes(original)
    with pytest.raises(Exception):
        install_api(config, "grok-fixture", grok_dir=home)
    assert settings.read_bytes() == original
    assert not config.exists()
    assert not (config.parent / "installation.json").exists()


def test_user_edits_block_reinstall_and_uninstall(api_paths):
    config, home, settings = api_paths
    install_api(config, "grok-fixture", grok_dir=home)
    edited = settings.read_bytes() + b"\n# user edit\n"
    settings.write_bytes(edited)
    for action in (
        lambda: install_api(config, "grok-other", grok_dir=home),
        lambda: uninstall_gateway(config),
    ):
        with pytest.raises(ValueError, match="settings changed"):
            action()
        assert settings.read_bytes() == edited
    assert (config.parent / "installation.json").exists()


def test_other_grok_home_manifest_does_not_bypass_model_collision(api_paths, tmp_path):
    config, home, settings = api_paths
    install_api(config, "grok-fixture", grok_dir=home)
    first_installed = settings.read_bytes()
    manifest = (config.parent / "installation.json").read_bytes()
    other = tmp_path / "Other Grok home"
    other.mkdir()
    other_settings = other / "config.toml"
    personal = b'[model.secure_mcp_xai]\nmodel = "personal"\n'
    other_settings.write_bytes(personal)
    with pytest.raises(ValueError, match="already exists"):
        install_api(config, "grok-other", grok_dir=other)
    assert other_settings.read_bytes() == personal
    assert settings.read_bytes() == first_installed
    assert (config.parent / "installation.json").read_bytes() == manifest


def test_write_failure_keeps_original_and_removes_manifest(api_paths, monkeypatch):
    from secure_mcp import gateway_install

    config, home, settings = api_paths
    original = b'default_model = "personal"\n'
    settings.write_bytes(original)

    def fail_write(*_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(gateway_install, "atomic_bytes", fail_write)
    with pytest.raises(OSError, match="disk failure"):
        install_api(config, "grok-fixture", grok_dir=home)
    assert settings.read_bytes() == original
    assert not (config.parent / "installation.json").exists()


@pytest.mark.parametrize(
    "model, port",
    [
        ("", 38118),
        ("  ", 38118),
        ("grok\ninvalid", 38118),
        ("grok", 80),
        ("grok", 65536),
    ],
)
def test_invalid_setup_rejected_before_writes(api_paths, model, port):
    config, home, settings = api_paths
    with pytest.raises(ValueError):
        install_api(config, model, port=port, grok_dir=home)
    assert not config.exists()
    assert not settings.exists()


def test_api_cli_install_and_uninstall_with_explicit_paths(api_paths):
    config, home, settings = api_paths
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "gateway",
            "api-install",
            "--provider",
            "xai",
            "--model",
            "grok-fixture",
            "--grok-home",
            str(home),
            "--config",
            str(config),
            "--port",
            "38119",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_api_config(config)["port"] == 38119
    result = runner.invoke(main, ["gateway", "api-uninstall", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert not settings.exists()


def test_api_config_refuses_subscription_mode(api_paths):
    config, _, _ = api_paths
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "port": 38118,
                "token": "t" * 32,
                "mode": "subscription",
                "provider": "xai",
            }
        )
    )
    with pytest.raises(ValueError, match="isolated xAI"):
        load_api_config(config)


def test_api_run_uses_only_fixed_xai_origin_and_separate_state(api_paths, monkeypatch):
    from secure_mcp import gateway

    config, home, _ = api_paths
    install_api(config, "grok-fixture", grok_dir=home)
    recorded = {}

    class FakeGateway:
        def __init__(self, address, token, **kwargs):
            recorded.update(address=address, token=token, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def serve_forever(self):
            recorded["served"] = True

    monkeypatch.setattr(gateway, "PrivacyGateway", FakeGateway)
    result = CliRunner().invoke(main, ["gateway", "api-run", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert recorded["address"] == ("127.0.0.1", 38118)
    assert recorded["token"] == load_api_config(config)["token"]
    assert recorded["upstreams"] == {}
    assert recorded["api_upstreams"] == {"xai": "https://api.x.ai"}
    assert recorded["state_dir"] == config.resolve().parent / "sessions"
    assert recorded["served"] is True


def test_api_install_requires_explicit_model_and_blocks_port_change(api_paths):
    config, home, settings = api_paths
    result = CliRunner().invoke(
        main,
        [
            "gateway",
            "api-install",
            "--provider",
            "xai",
            "--grok-home",
            str(home),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert "--model" in result.output
    assert not settings.exists()
    install_api(config, "grok-fixture", grok_dir=home)
    before = settings.read_bytes()
    with pytest.raises(ValueError, match="changing its port"):
        install_api(config, "grok-fixture", port=38119, grok_dir=home)
    assert settings.read_bytes() == before
