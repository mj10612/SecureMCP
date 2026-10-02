"""Codex-specific wire formats, installation scopes and local restoration."""

import json
import os
from pathlib import Path
import subprocess
import sys

from click.testing import CliRunner
import pytest

from secure_mcp.cli import main
from secure_mcp.hooks import (
    AGENT_EVENTS,
    HookStore,
    agent_state_dir,
    configure,
    handle_hook,
    installation_status,
)


@pytest.fixture
def codex_install(tmp_path):
    state = tmp_path / "private state" / "codex"
    settings = tmp_path / "project with spaces" / ".codex" / "hooks.json"
    configure(settings, state, True, agent="codex")
    return settings, state


def event(name, **fields):
    return {
        "hook_event_name": name,
        "session_id": "codex-thread-1",
        "tool_name": "Bash",
        **fields,
    }


@pytest.mark.parametrize(
    "text", ["Alice alice@example.com", "홍길동 hong@example.com", "삼성 Alice 150억원"]
)
@pytest.mark.parametrize("shape", ["string", "object"])
def test_codex_masked_feedback_and_local_argument_restoration(
    codex_install, text, shape
):
    _, state = codex_install
    response = text if shape == "string" else {"stdout": text, "exit_code": 7}
    result = handle_hook(event("PostToolUse", tool_response=response), state, "codex")
    assert result["continue"] is False
    assert "decision" not in result and "updatedToolOutput" not in json.dumps(result)
    masked = (
        result["stopReason"]
        if shape == "string"
        else json.loads(result["stopReason"])["stdout"]
    )
    assert masked != text
    if shape == "object":
        assert json.loads(result["stopReason"])["exit_code"] == 7
    restored = handle_hook(
        event("PreToolUse", tool_input={"command": masked}), state, "codex"
    )
    specific = restored["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert specific["updatedInput"]["command"] == text
    assert "updatedPermissions" not in specific


def test_codex_patch_restoration(codex_install):
    _, state = codex_install
    masked = handle_hook(
        event("PostToolUse", tool_response="alice@example.com"), state, "codex"
    )["stopReason"]
    patch = f"*** Begin Patch\n*** Add File: local.txt\n+{masked}\n*** End Patch"
    result = handle_hook(
        event("PreToolUse", tool_name="apply_patch", tool_input={"command": patch}),
        state,
        "codex",
    )
    assert (
        "alice@example.com" in result["hookSpecificOutput"]["updatedInput"]["command"]
    )


def test_codex_never_restores_remote_tool_args(codex_install):
    _, state = codex_install
    masked = handle_hook(
        event("PostToolUse", tool_response="alice@example.com"), state, "codex"
    )["stopReason"]
    with pytest.raises(ValueError, match="restricted"):
        handle_hook(
            event(
                "PreToolUse", tool_name="mcp__remote__send", tool_input={"text": masked}
            ),
            state,
            "codex",
        )
    assert (
        handle_hook(
            event("PreToolUse", tool_name="update_plan", tool_input={"plan": []}),
            state,
            "codex",
        )
        == {}
    )


def test_codex_only_registers_supported_events_and_keeps_others(codex_install):
    settings, state = codex_install
    original = json.loads(settings.read_text())
    original["description"] = "existing workspace hooks"
    original["hooks"]["Stop"] = [
        {"hooks": [{"type": "command", "command": "echo done"}]}
    ]
    settings.write_text(json.dumps(original), encoding="utf-8")
    configure(settings, state, True, "codex")
    data = json.loads(settings.read_text())
    assert data["description"] == original["description"]
    assert data["hooks"]["Stop"] == original["hooks"]["Stop"]
    assert "MessageDisplay" not in data["hooks"]
    assert data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 3
    assert installation_status(settings, state, "codex")["ready"]
    for name in AGENT_EVENTS["codex"]:
        assert len(data["hooks"][name]) == 1
    configure(settings, state, False, "codex")
    assert json.loads(settings.read_text())["hooks"] == {
        "Stop": original["hooks"]["Stop"]
    }


def test_installer_does_not_remove_other_agent_hooks(codex_install):
    settings, state = codex_install
    configure(settings, state, True, "claude")
    configure(settings, state, False, "codex")
    assert installation_status(settings, state, "claude")["ready"]
    assert not installation_status(settings, state, "codex")["ready"]


def test_codex_cli_install_diagnostics_restore_and_remove(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    base = tmp_path / "private state"
    args = ["--agent", "codex", "--state-dir", str(base)]
    install = runner.invoke(main, ["init", *args])
    assert install.exit_code == 0 and "/hooks" in install.output
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert not (tmp_path / ".claude").exists()
    status = runner.invoke(main, ["doctor", *args])
    assert status.exit_code == 0
    assert json.loads(status.output)["display_restoration"] is False
    state = agent_state_dir(base, "codex")
    masked = handle_hook(
        event("PostToolUse", tool_response="홍길동 alice@example.com"), state, "codex"
    )["stopReason"]
    restore = runner.invoke(
        main, ["restore", *args, "--session-id", "codex-thread-1"], input=masked
    )
    assert restore.exit_code == 0 and restore.output == "홍길동 alice@example.com"
    stats = runner.invoke(main, ["stats", *args, "--session-id", "codex-thread-1"])
    assert stats.exit_code == 0 and "alice@example.com" not in stats.output
    assert runner.invoke(main, ["uninstall", *args]).exit_code == 0
    assert runner.invoke(main, ["doctor", *args]).exit_code == 1


def test_codex_global_install_honors_codex_home(tmp_path, monkeypatch):
    home = tmp_path / "custom codex home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    args = ["--agent", "codex", "--global", "--state-dir", str(tmp_path / "state")]
    runner = CliRunner()
    assert runner.invoke(main, ["init", *args]).exit_code == 0
    assert (home / "hooks.json").exists()
    assert runner.invoke(main, ["doctor", *args]).exit_code == 0
    assert runner.invoke(main, ["uninstall", *args]).exit_code == 0


def test_codex_and_claude_same_session_id_are_isolated(tmp_path):
    base = tmp_path / "state"
    configure(tmp_path / "claude.json", base, True)
    configure(tmp_path / "codex.json", agent_state_dir(base, "codex"), True, "codex")
    handle_hook(event("PostToolUse", tool_response="Alice"), base)
    assert (
        handle_hook(
            event("PreToolUse", tool_input={"command": "public"}),
            base / "codex",
            "codex",
        )
        == {}
    )
    with pytest.raises(ValueError, match="Unrecognized"):
        handle_hook(
            event("PreToolUse", tool_input={"command": "⟦ENT_1⟧"}),
            base / "codex",
            "codex",
        )


def test_codex_errors_withhold_output_and_deny_bad_restore(codex_install):
    _, state = codex_install
    runner = CliRunner()
    args = ["hook", "--agent", "codex", "--state-dir", str(state)]
    (state / "key").unlink()
    result = runner.invoke(
        main,
        args,
        input=json.dumps(event("PostToolUse", tool_response="private original")),
    )
    assert result.exit_code == 0 and json.loads(result.output)["continue"] is False
    assert "private original" not in result.output
    result = runner.invoke(
        main,
        args,
        input=json.dumps(
            event("PreToolUse", tool_input={"command": "private original"})
        ),
    )
    assert (
        json.loads(result.output)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )
    assert "private original" not in result.output


def test_codex_does_not_advertise_display_hook(codex_install):
    _, state = codex_install
    with pytest.raises(ValueError, match="Unsupported"):
        handle_hook(event("MessageDisplay", delta="Alice"), state, "codex")


def test_codex_empty_output_and_cleanup(codex_install):
    _, state = codex_install
    result = handle_hook(event("PostToolUse", tool_response=""), state, "codex")
    assert result["continue"] is False and result["stopReason"]
    handle_hook(event("SessionEnd"), state, "codex")
    assert not HookStore(state).path("codex-thread-1").exists()


def test_codex_real_hook_subprocess_uses_registered_command(codex_install):
    settings, state = codex_install
    command = json.loads(settings.read_text())["hooks"]["PostToolUse"][0]["hooks"][0][
        "command"
    ]
    import shlex

    args = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        args = [arg.strip('"') for arg in args]
    assert Path(args[0]) == Path(sys.executable)
    result = subprocess.run(
        args,
        input=json.dumps(
            event("PostToolUse", tool_response="홍길동 alice@example.com"),
            ensure_ascii=False,
        ),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert (
        output["continue"] is False and "alice@example.com" not in output["stopReason"]
    )
