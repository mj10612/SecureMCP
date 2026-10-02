"""Exercise hook installation and encrypted multi-process tool roundtrips."""

import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from secure_mcp.cli import main
from secure_mcp.encrypted_session import session_file_lock
from secure_mcp.hooks import (
    EVENTS,
    HookStore,
    configure,
    handle_hook,
    installation_status,
)


@pytest.fixture
def installed(tmp_path):
    settings = tmp_path / "project with spaces" / "settings.json"
    state = tmp_path / "private state"
    configure(settings, state, True)
    return settings, state


def event(name, **fields):
    return {
        "hook_event_name": name,
        "session_id": "conversation-1",
        "tool_name": "Write",
        **fields,
    }


def test_install_reinstall_and_uninstall_preserve_settings(tmp_path):
    settings = tmp_path / "settings.json"
    original = {
        "permissions": {"deny": ["Bash(rm *)"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "rtk hook"}],
                }
            ]
        },
    }
    raw = json.dumps(original).encode()
    settings.write_bytes(raw)
    state = tmp_path / "state"
    backup = configure(settings, state, True)
    assert backup.read_bytes() == raw
    key = (state / "key").read_bytes()
    configure(settings, state, True)
    assert (state / "key").read_bytes() == key
    config = json.loads(settings.read_text())
    assert len(config["hooks"]["PreToolUse"]) == 2
    assert installation_status(settings, state)["ready"]
    for name in EVENTS:
        assert name in config["hooks"]
    configure(settings, state, False)
    assert json.loads(settings.read_text()) == original
    assert not installation_status(settings, state)["ready"]


@pytest.mark.parametrize(
    "bad", ["{", "[]", '{"hooks": []}', '{"hooks": {"PreToolUse": [3]}}']
)
def test_invalid_settings_are_never_overwritten(tmp_path, bad):
    path = tmp_path / "settings.json"
    path.write_text(bad)
    with pytest.raises(ValueError):
        configure(path, tmp_path / "state", True)
    assert path.read_text() == bad
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "text",
    [
        "Alice emailed alice@example.com at Google.",
        "홍길동 고객의 이메일은 hong@example.com입니다.",
        "삼성 고객 Alice: alice@example.com, 150억원",
    ],
)
def test_tool_roundtrip_and_display(installed, text):
    _, state = installed
    response = {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}
    masked = handle_hook(
        event("PostToolUse", tool_name="Bash", tool_response=response), state
    )
    result = masked["hookSpecificOutput"]["updatedToolOutput"]
    assert result["stdout"] != text
    assert "alice@example.com" not in result["stdout"]
    assert result["interrupted"] is False and result["isImage"] is False
    # A fresh HookStore/CLI process loads the same encrypted mappings.
    restored = handle_hook(
        event("PreToolUse", tool_input={"content": result["stdout"]}), state
    )
    assert restored["hookSpecificOutput"]["updatedInput"]["content"] == text
    assert "permissionDecision" not in restored["hookSpecificOutput"]
    display = handle_hook(event("MessageDisplay", delta=result["stdout"]), state)
    assert display["hookSpecificOutput"]["displayContent"] == text
    snapshot = HookStore(state).path("conversation-1").read_bytes()
    assert text.encode() not in snapshot
    handle_hook(event("SessionEnd"), state)
    assert not HookStore(state).path("conversation-1").exists()


def test_output_shape_binary_and_discriminators_are_preserved(installed):
    _, state = installed
    response = {
        "content": [
            {"type": "text", "text": "Alice alice@example.com"},
            {"type": "image", "data": "SecretBinaryBytes", "mimeType": "image/png"},
        ],
        "count": 42,
        "isError": False,
    }
    masked = handle_hook(event("PostToolUse", tool_response=response), state)[
        "hookSpecificOutput"
    ]["updatedToolOutput"]
    assert masked["content"][0]["type"] == "text"
    assert masked["content"][0]["text"] != response["content"][0]["text"]
    assert masked["content"][1] == response["content"][1]
    assert masked["count"] == 42 and masked["isError"] is False
    image = {
        "stdout": "SecretBinaryBytes",
        "stderr": "",
        "isImage": True,
        "interrupted": False,
    }
    result = handle_hook(event("PostToolUse", tool_response=image), state)
    assert result["hookSpecificOutput"]["updatedToolOutput"] == image


def test_doctor_rejects_a_different_state_directory(installed, tmp_path):
    settings, _ = installed
    wrong_state = tmp_path / "other-state"
    wrong_state.mkdir()
    (wrong_state / "key").write_text("another key")
    assert not installation_status(settings, wrong_state)["ready"]


def test_repeated_outputs_keep_stable_placeholders(installed):
    _, state = installed
    first = handle_hook(event("PostToolUse", tool_response={"stdout": "Alice"}), state)
    text = first["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    second = handle_hook(
        event("PostToolUse", tool_response={"stdout": f"{text} Alice"}), state
    )
    assert (
        second["hookSpecificOutput"]["updatedToolOutput"]["stdout"] == f"{text} {text}"
    )


def test_remote_tools_never_receive_restored_originals(installed):
    _, state = installed
    handle_hook(event("PostToolUse", tool_response={"stdout": "Alice"}), state)
    with pytest.raises(ValueError, match="restricted"):
        handle_hook(
            event(
                "PreToolUse",
                tool_name="mcp__remote__send",
                tool_input={"text": "⟦ENT_1⟧"},
            ),
            state,
        )
    assert (
        handle_hook(
            event("PreToolUse", tool_name="WebSearch", tool_input={"query": "public"}),
            state,
        )
        == {}
    )


def test_sessions_are_isolated_and_paths_cannot_escape(installed):
    _, state = installed
    handle_hook(event("PostToolUse", tool_response={"stdout": "Alice"}), state)
    with pytest.raises(ValueError, match="Unrecognized"):
        handle_hook(
            {
                **event("PreToolUse", tool_input={"command": "⟦ENT_1⟧"}),
                "session_id": "other",
            },
            state,
        )
    assert HookStore(state).path("../../outside").parent == state


def test_missing_state_plain_input_and_unknown_placeholder(installed):
    _, state = installed
    assert (
        handle_hook(event("PreToolUse", tool_input={"command": "git status"}), state)
        == {}
    )
    with pytest.raises(ValueError, match="Unrecognized"):
        handle_hook(event("PreToolUse", tool_input={"command": "⟦ENT_99⟧"}), state)


def test_concurrent_writer_refused_without_overwrite(installed):
    _, state = installed
    path = HookStore(state).path("conversation-1")
    with session_file_lock(path), pytest.raises(ValueError, match="in use"):
        handle_hook(event("PostToolUse", tool_response={"stdout": "Alice"}), state)
    assert not path.exists()


def test_pre_hook_failure_denies_without_echoing_originals(installed):
    _, state = installed
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["hook", "--state-dir", str(state)],
        input=json.dumps(
            event("PreToolUse", tool_input={"content": "private original ⟦ENT_99⟧"})
        ),
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "private original" not in result.output


def test_invalid_hook_failure_has_no_payload(installed):
    _, state = installed
    result = CliRunner().invoke(
        main, ["hook", "--state-dir", str(state)], input="private secret"
    )
    assert result.exit_code == 1
    assert "private secret" not in result.output


def test_exec_masks_both_streams_and_preserves_exit_status(installed):
    _, state = installed
    script = "import sys; print('Alice alice@example.com'); print('Bob bob@example.com', file=sys.stderr); sys.exit(7)"
    result = CliRunner().invoke(
        main,
        [
            "exec",
            "--state-dir",
            str(state),
            "--session-id",
            "conversation-1",
            "--",
            sys.executable,
            "-c",
            script,
        ],
    )
    assert result.exit_code == 7
    assert (
        "alice@example.com" not in result.output
        and "bob@example.com" not in result.output
    )
    assert "⟦ENT_" in result.output
    with HookStore(state).session("conversation-1") as session:
        assert session.total_mask_calls == 2


def test_exec_masking_failure_never_prints_raw_output(installed):
    _, state = installed
    (state / "key").unlink()
    result = CliRunner().invoke(
        main,
        [
            "exec",
            "--state-dir",
            str(state),
            "--session-id",
            "conversation-1",
            "--",
            sys.executable,
            "-c",
            "print('private original')",
        ],
    )
    assert result.exit_code == 1
    assert "private original" not in result.output


def test_cli_project_install_doctor_stats_uninstall(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    state = tmp_path / "state"
    args = ["--agent", "claude", "--state-dir", str(state)]
    assert runner.invoke(main, ["doctor", *args]).exit_code == 1
    assert runner.invoke(main, ["init", *args]).exit_code == 0
    assert runner.invoke(main, ["doctor", *args]).exit_code == 0
    handle_hook(event("PostToolUse", tool_response={"stdout": "Alice"}), state)
    result = runner.invoke(
        main, ["stats", "--state-dir", str(state), "--session-id", "conversation-1"]
    )
    assert result.exit_code == 0 and "Alice" not in result.output
    assert json.loads(result.output)["total_unique_mappings"] == 1
    assert runner.invoke(main, ["uninstall", *args]).exit_code == 0
    assert runner.invoke(main, ["doctor", *args]).exit_code == 1


def test_real_process_stdin_stdout_roundtrip(installed):
    _, state = installed
    command = [sys.executable, "-m", "secure_mcp", "hook", "--state-dir", str(state)]

    def run(payload):
        result = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        assert result.stderr == ""
        return json.loads(result.stdout)["hookSpecificOutput"]

    original = "홍길동 and Alice at alice@example.com"
    masked = run(event("PostToolUse", tool_response={"stdout": original}))[
        "updatedToolOutput"
    ]["stdout"]
    assert run(event("MessageDisplay", delta=masked))["displayContent"] == original


@pytest.mark.parametrize(
    "payload",
    [
        {},
        event("Unknown"),
        event("PreToolUse"),
        event("PostToolUse"),
        event("MessageDisplay"),
        {"hook_event_name": "SessionEnd", "session_id": 42},
    ],
)
def test_invalid_event_input(installed, payload):
    _, state = installed
    with pytest.raises(ValueError):
        handle_hook(payload, state)
