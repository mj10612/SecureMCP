"""Local Claude Code and Codex hooks: partial tool-text protection."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator

from secure_mcp.encrypted_session import load_session, save_session, session_file_lock
from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.models import MaskMode, SurrogateStrategy
from secure_mcp.session import PrivacySession


EVENTS = ("PreToolUse", "PostToolUse", "MessageDisplay", "SessionEnd")
AGENT_EVENTS = {
    "claude": EVENTS,
    "codex": ("PreToolUse", "PostToolUse", "SessionEnd"),
}
MARKER = "-m secure_mcp hook --state-dir"
# Preserve protocol discriminators, IDs, binary data and JSON scalar types.
TEXT_FIELDS = frozenset(
    {
        "stdout",
        "stderr",
        "text",
        "content",
        "output",
        "filePath",
        "file_path",
        "filename",
        "filenames",
        "matches",
        "line",
        "lines",
        "message",
    }
)
OPAQUE_FIELDS = frozenset(
    {
        "type",
        "mimeType",
        "mime_type",
        "data",
        "signature",
        "id",
        "tool_use_id",
        "encoding",
    }
)
LOCAL_TOOLS = frozenset(
    {"Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit"}
)


def default_state_dir() -> Path:
    return Path.home() / ".secure-mcp" / "hooks"


def agent_state_dir(directory: Path, agent: str) -> Path:
    """Keep existing Claude snapshots in place; isolate new Codex sessions."""
    return directory / "codex" if agent == "codex" else directory


def _atomic_json(path: Path, value: Any) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".secure-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _settings(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError("Invalid agent settings; no settings were overwritten.")
    for entries in value.get("hooks", {}).values():
        if not isinstance(entries, list):
            raise ValueError("Invalid hook registry; no settings were overwritten.")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError("Invalid hook entry; no settings were overwritten.")
            if not all(isinstance(h, dict) for h in entry["hooks"]):
                raise ValueError("Invalid hook command; no settings were overwritten.")
    return value


def _owned(hook: dict[str, Any], agent: str | None = None) -> bool:
    command = hook.get("command")
    if (
        hook.get("type") != "command"
        or not isinstance(command, str)
        or MARKER not in command
    ):
        return False
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    if parts[1:5] != ["-m", "secure_mcp", "hook", "--state-dir"]:
        return False
    owner = "claude" if len(parts) == 6 else None
    if len(parts) == 8 and parts[6] == "--agent" and parts[7] in AGENT_EVENTS:
        owner = parts[7]
    return owner is not None and (agent is None or owner == agent)


def _remove_owned(config: dict[str, Any], agent: str) -> None:
    registry = config.get("hooks", {})
    for event in list(registry):
        remaining = []
        for entry in registry[event]:
            kept = [h for h in entry["hooks"] if not _owned(h, agent)]
            if kept:
                remaining.append({**entry, "hooks": kept})
        if remaining:
            registry[event] = remaining
        else:
            del registry[event]


def configure(
    settings: Path, state_dir: Path, install: bool, agent: str = "claude"
) -> Path | None:
    """Merge only our hooks; back up exact previous bytes and preserve other settings."""
    if agent not in AGENT_EVENTS:
        raise ValueError("Unsupported agent.")
    settings.parent.mkdir(parents=True, exist_ok=True)
    with session_file_lock(settings):
        config = _settings(settings)
        _remove_owned(config, agent)
        if install:
            state_dir = state_dir.resolve()
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            key = state_dir / "key"
            if not key.exists():
                fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as stream:
                    stream.write(secrets.token_urlsafe(32))
            args = [
                sys.executable,
                "-m",
                "secure_mcp",
                "hook",
                "--state-dir",
                str(state_dir),
            ]
            if agent != "claude":
                args.extend(["--agent", agent])
            command = (
                subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
            )
            registry = config.setdefault("hooks", {})
            for event in AGENT_EVENTS[agent]:
                registry.setdefault(event, []).append(
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                                "timeout": 3
                                if agent == "codex" and event == "SessionEnd"
                                else 10,
                            }
                        ]
                    }
                )
        backup = None
        if settings.exists():
            backup = settings.with_name(
                settings.name + f".secure-mcp-{time.time_ns()}.bak"
            )
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(settings.read_bytes())
        _atomic_json(settings, config)
        return backup


def installation_status(
    settings: Path, state_dir: Path, agent: str = "claude"
) -> dict[str, Any]:
    if agent not in AGENT_EVENTS:
        raise ValueError("Unsupported agent.")
    config = _settings(settings)
    registry = config.get("hooks", {})
    installed = [
        event
        for event in AGENT_EVENTS[agent]
        if any(
            _working_registration(h, state_dir, agent)
            for entry in registry.get(event, [])
            for h in entry["hooks"]
        )
    ]
    return {
        "installed_events": installed,
        "key_present": (state_dir / "key").is_file(),
        "ready": len(installed) == len(AGENT_EVENTS[agent])
        and (state_dir / "key").is_file(),
        "scope": "tool text only; prompts, attachments and telemetry are not covered",
        "agent": agent,
        "display_restoration": agent == "claude",
        "host_trust": "Review installed hooks using /hooks"
        if agent == "codex"
        else "Check host trust settings",
    }


def _working_registration(hook: dict[str, Any], state_dir: Path, agent: str) -> bool:
    if not _owned(hook, agent):
        return False
    parts = shlex.split(hook["command"], posix=os.name != "nt")
    return (
        Path(parts[0].strip('"')).is_file()
        and Path(parts[5].strip('"')).resolve() == state_dir.resolve()
    )


class HookStore:
    """Encrypted snapshots shared by short-lived hook processes; no HTTP service."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.engine = MaskingEngine()

    def path(self, sid: str) -> Path:
        if not isinstance(sid, str) or not sid.strip() or len(sid) > 1024:
            raise ValueError("A valid host session ID is required.")
        return self.directory / (sha256(sid.encode("utf-8")).hexdigest() + ".enc")

    @contextmanager
    def session(
        self, sid: str, create: bool = False
    ) -> Iterator[PrivacySession | None]:
        path = self.path(sid)
        password = (self.directory / "key").read_text(encoding="ascii")
        if not password:
            raise ValueError("Missing local encryption key.")
        with session_file_lock(path):
            session = load_session(path, password, sid) if path.exists() else None
            if session is None and create:
                session = PrivacySession(
                    sid, MaskMode.ENTITIES_ONLY, SurrogateStrategy.UNICODE
                )
            try:
                yield session
                if session is not None:
                    save_session(path, session, password)
            finally:
                if session is not None:
                    session.clear()

    def mask(self, text: str, session: PrivacySession) -> str:
        # Tool output can echo values the model already saw. Never nest placeholders.
        pieces = []
        end = 0
        for match in StrategyGenerator.get_pattern(session.strategy).finditer(text):
            pieces.append(self._mask_piece(text[end : match.start()], session))
            pieces.append(match.group())
            end = match.end()
        pieces.append(self._mask_piece(text[end:], session))
        session.total_mask_calls += 1
        session.touch()
        return "".join(pieces)

    def _mask_piece(self, text: str, session: PrivacySession) -> str:
        return self.engine.mask_text(
            text,
            session.session_id,
            session.generator,
            session.forward_store,
            session.reverse_store,
            mode=MaskMode.ENTITIES_ONLY,
        ).masked_text

    def restore(self, text: str, session: PrivacySession | None) -> str:
        result = self.engine.unmask(
            text,
            session.session_id if session else "missing",
            session.reverse_store if session else {},
            SurrogateStrategy.UNICODE,
            strict=True,
        )
        if session:
            session.total_unmask_calls += 1
            session.touch()
        return result.unmasked_text

    def clear(self, sid: str) -> None:
        path = self.path(sid)
        with session_file_lock(path):
            path.unlink(missing_ok=True)


def _walk(
    value: Any,
    transform: Callable[[str], str],
    all_strings: bool = False,
    preserve_protocol: bool = False,
) -> Any:
    if isinstance(value, str):
        return transform(value) if all_strings else value
    if isinstance(value, list):
        return [
            _walk(item, transform, all_strings, preserve_protocol) for item in value
        ]
    if isinstance(value, dict):
        return {
            key: item
            if preserve_protocol
            and (
                key in OPAQUE_FIELDS
                or (key == "stdout" and value.get("isImage") is True)
            )
            else _walk(
                item, transform, all_strings or key in TEXT_FIELDS, preserve_protocol
            )
            for key, item in value.items()
        }
    return value


def handle_hook(
    payload: dict[str, Any], state_dir: Path, agent: str = "claude"
) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    if agent not in AGENT_EVENTS or event not in AGENT_EVENTS[agent]:
        raise ValueError("Unsupported hook event.")
    sid = payload.get("session_id")
    if not isinstance(sid, str):
        raise ValueError("A host session ID is required.")
    store = HookStore(state_dir)
    store.path(sid)
    if event == "SessionEnd":
        store.clear(sid)
        return {}
    if event == "PostToolUse":
        if "tool_response" not in payload:
            raise ValueError("Missing tool response.")
        with store.session(sid, create=True) as session:
            assert session is not None
            replacement = _walk(
                payload["tool_response"],
                lambda text: store.mask(text, session),
                isinstance(payload["tool_response"], (str, list)),
                preserve_protocol=True,
            )
        if agent == "codex":
            # Codex does not support Claude's updatedToolOutput. Feedback replacement
            # also reaches code-mode tool calls without rejecting the nested promise.
            text = (
                replacement
                if isinstance(replacement, str)
                else json.dumps(replacement, ensure_ascii=False)
            )
            return {
                "continue": False,
                "stopReason": text or "SecureMCP: empty tool output.",
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "updatedToolOutput": replacement,
            }
        }
    with store.session(sid) as session:
        if event == "PreToolUse":
            if not isinstance(payload.get("tool_input"), dict):
                raise ValueError("Missing tool input object.")
            local_tools = {"Bash", "apply_patch"} if agent == "codex" else LOCAL_TOOLS
            if payload.get("tool_name") not in local_tools:
                # Never restore originals into remote MCP/network tools.
                if StrategyGenerator.get_pattern(SurrogateStrategy.UNICODE).search(
                    json.dumps(payload["tool_input"], ensure_ascii=False)
                ):
                    raise ValueError("Restoration is restricted to local tools.")
                return {}
            restored = _walk(
                payload["tool_input"], lambda text: store.restore(text, session), True
            )
            if restored == payload["tool_input"]:
                return {}
            if agent == "codex":
                if not isinstance(restored.get("command"), str):
                    raise ValueError("Codex local tool requires a command string.")
                # Codex requires allow alongside updatedInput. This is PreToolUse,
                # not a PermissionRequest approval; sandbox/approval policy is retained.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "permissionDecision": "allow",
                        "updatedInput": restored,
                    }
                }
            # Never grant permission: the host must run its normal approval checks.
            return {
                "hookSpecificOutput": {"hookEventName": event, "updatedInput": restored}
            }
        delta = payload.get("delta")
        if not isinstance(delta, str):
            raise ValueError("Missing display delta.")
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "displayContent": store.restore(delta, session),
            }
        }
