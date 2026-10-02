"""Trusted local CLI with opt-in encrypted session transfer."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import json
import subprocess
import sys
import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from secure_mcp.encrypted_session import load_session, save_session, session_file_lock
from secure_mcp.models import MaskMode, SurrogateStrategy
from secure_mcp.server import app, engine, vault
from secure_mcp.session import PrivacySession
from secure_mcp.hooks import (
    configure,
    default_state_dir,
    handle_hook,
    installation_status,
)

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
console = Console(legacy_windows=False)


class LiteralArgumentGroup(click.Group):
    def main(self, *args, **kwargs):
        kwargs["windows_expand_args"] = False
        return super().main(*args, **kwargs)


def _file_options(command):
    command = click.option(
        "--session-file", type=click.Path(dir_okay=False, path_type=Path)
    )(command)
    return click.option(
        "--session-password", envvar="SECURE_MCP_SESSION_PASSWORD", hide_input=True
    )(command)


def _check_strategy(session, strategy):
    if session.strategy != strategy:
        raise ValueError(
            f"Session uses strategy '{session.strategy.value}'; use a new session ID/file to switch to '{strategy.value}'."
        )


@contextmanager
def _session(sid, path, password, mode=None, strategy=None):
    try:
        session: PrivacySession | None
        with session_file_lock(path) if path else nullcontext():
            if path:
                password = password or click.prompt(
                    "Session file password", hide_input=True
                )
                if path.exists():
                    session = load_session(path, password, sid)
                elif mode is not None:
                    session = PrivacySession(sid, mode, strategy)
                else:
                    raise ValueError("Encrypted session file does not exist.")
            else:
                session = (
                    vault.get_or_create(sid, mode, strategy)
                    if mode is not None
                    else vault.get_session(sid)
                )
            if session is None:
                raise ValueError(
                    f"Session '{sid}' not found. For separate CLI invocations, pass the same --session-file to both mask and unmask."
                )
            if strategy is not None:
                _check_strategy(session, strategy)
            try:
                with session.operation():
                    yield session
                    if path:
                        save_session(path, session, password)
            finally:
                if path:
                    session.clear()
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@click.group(cls=LiteralArgumentGroup)
@click.version_option(version="0.3.0")
def main():
    """SecureMCP: local English/Korean masking. Mask BEFORE sending data to a provider."""


@main.command()
@click.option("--transport", type=click.Choice(["stdio"]), default="stdio")
def serve(transport):
    """Run local MCP utilities. Network transports are disabled without authentication."""
    try:
        app.run(transport=transport)
    finally:
        vault.close()


def _hook_options(command):
    command = click.option("--agent", type=click.Choice(["claude"]), default="claude")(
        command
    )
    command = click.option("--global", "global_scope", is_flag=True)(command)
    return click.option(
        "--state-dir", type=click.Path(path_type=Path), default=default_state_dir
    )(command)


def _hook_settings(global_scope):
    return (
        Path.home() / ".claude" / "settings.json"
        if global_scope
        else Path.cwd() / ".claude" / "settings.local.json"
    )


@main.command("init")
@_hook_options
def init_hooks(agent, global_scope, state_dir):
    """Install local Claude tool hooks. Prompts/attachments are not intercepted."""
    try:
        backup = configure(_hook_settings(global_scope), state_dir, install=True)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        "Claude tool hooks installed. Restart Claude Code; run secure-mcp doctor."
    )
    click.echo(
        "Scope: tool text only. Direct prompts, attachments and telemetry are not covered."
    )
    if backup:
        click.echo(f"Settings backup: {backup}")


@main.command("uninstall")
@_hook_options
def uninstall_hooks(agent, global_scope, state_dir):
    """Remove only SecureMCP hooks; preserve other settings and encrypted state."""
    try:
        configure(_hook_settings(global_scope), state_dir, install=False)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        "SecureMCP hooks removed. Restart Claude Code. Encrypted state is retained."
    )


@main.command()
@_hook_options
def doctor(agent, global_scope, state_dir):
    """Inspect hook registration. Host-version compatibility needs a real smoke test."""
    try:
        status = installation_status(_hook_settings(global_scope), state_dir)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(status, ensure_ascii=False))
    if not status["ready"]:
        raise click.ClickException("Incomplete hook installation.")


@main.command()
@click.option("--state-dir", type=click.Path(path_type=Path), default=default_state_dir)
@click.option("--session-id", required=True)
def stats(state_dir, session_id):
    """Show local hook session counters, without original values or mappings."""
    from secure_mcp.hooks import HookStore

    try:
        with HookStore(state_dir).session(session_id) as session:
            click.echo(session.get_stats().model_dump_json() if session else "{}")
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@main.command(hidden=True)
@click.option("--state-dir", type=click.Path(path_type=Path), required=True)
def hook(state_dir):
    """JSON stdin/stdout entry point invoked by Claude Code, never by an MCP model."""
    payload = None
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Expected a hook object.")
        result = handle_hook(payload, state_dir)
        click.echo(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, TypeError, AssertionError):
        # Do not echo raw payloads or exception messages containing secret candidates.
        if isinstance(payload, dict) and payload.get("hook_event_name") == "PreToolUse":
            click.echo(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "SecureMCP restoration failed; check local state.",
                        }
                    }
                )
            )
        else:
            click.echo(
                "SecureMCP hook failed; original host output may be used.", err=True
            )
            raise click.exceptions.Exit(1)


@main.command("exec", context_settings={"ignore_unknown_options": True})
@click.option("--state-dir", type=click.Path(path_type=Path), default=default_state_dir)
@click.option("--session-id", required=True)
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
def exec_masked(state_dir, session_id, command):
    """Run executable + arguments locally; return only masked stdout/stderr (no implicit shell)."""
    from secure_mcp.hooks import HookStore

    store = HookStore(state_dir)
    try:
        with store.session(session_id) as session:
            restored = [store.restore(arg, session) for arg in command]
        completed = subprocess.run(restored, capture_output=True, encoding="utf-8")
        with store.session(session_id, create=True) as session:
            assert session is not None
            stdout = store.mask(completed.stdout, session)
            stderr = store.mask(completed.stderr, session)
    except (ValueError, OSError, AssertionError):
        raise click.ClickException(
            "Local execution/masking failed; no raw output was returned."
        ) from None
    click.echo(stdout, nl=False)
    click.echo(stderr, nl=False, err=True)
    raise click.exceptions.Exit(
        completed.returncode
        if completed.returncode >= 0
        else 128 - completed.returncode
    )


@main.command()
@click.argument("text")
@click.option("--session-id", default="cli_session")
@click.option(
    "--mode", type=click.Choice([m.value for m in MaskMode]), default="content_words"
)
@click.option(
    "--strategy",
    type=click.Choice([s.value for s in SurrogateStrategy]),
    default="bracket",
)
@click.option("--language", type=click.Choice(["auto", "en", "ko"]), default="auto")
@click.option("--code-language", default="auto")
@click.option("--sensitive-term", multiple=True)
@click.option("--preserve", multiple=True)
@click.option("--json-output", is_flag=True)
@_file_options
def mask(
    text,
    session_id,
    mode,
    strategy,
    language,
    code_language,
    sensitive_term,
    preserve,
    json_output,
    session_file,
    session_password,
):
    """Mask locally; --json-output emits the masked payload without displaying raw input."""
    mask_mode, strat = MaskMode(mode), SurrogateStrategy(strategy)
    with _session(
        session_id, session_file, session_password, mask_mode, strat
    ) as session:
        result = engine.mask_text(
            text,
            session.session_id,
            session.generator,
            session.forward_store,
            session.reverse_store,
            mode=mask_mode,
            language=language,
            code_language=code_language,
            sensitive_terms=set(sensitive_term),
            custom_preserve=set(preserve),
        )
        session.mode = mask_mode
        session.total_mask_calls += 1
    if json_output:
        click.echo(result.model_dump_json())
    else:
        console.print(Panel(Text(text), title="Original Input"))
        console.print(Panel(Text(result.masked_text), title="Masked Output"))
        console.print(f"Masked occurrence ratio: {result.masked_ratio:.1%}")


@main.command()
@click.argument("masked_text")
@click.option("--session-id", default="cli_session")
@click.option(
    "--strategy", type=click.Choice([s.value for s in SurrogateStrategy]), default=None
)
@click.option(
    "--normalize-particles",
    is_flag=True,
    help="Adjust Korean particles in generated responses; off for exact roundtrips.",
)
@click.option("--strict", is_flag=True)
@click.option("--json-output", is_flag=True)
@_file_options
def unmask(
    masked_text,
    session_id,
    strategy,
    normalize_particles,
    strict,
    json_output,
    session_file,
    session_password,
):
    """Restore for local display. Never send this output back into the model context."""
    strat = SurrogateStrategy(strategy) if strategy is not None else None
    with _session(
        session_id, session_file, session_password, strategy=strat
    ) as session:
        result = engine.unmask(
            masked_text,
            session.session_id,
            session.reverse_store,
            strat or session.strategy,
            normalize_particles,
            strict,
        )
        session.total_unmask_calls += 1
    if json_output:
        click.echo(result.model_dump_json())
    else:
        console.print(Panel(Text(result.unmasked_text), title="Restored Original Text"))
        console.print(
            f"Restored occurrences: {result.restored_occurrences}; unique tokens: {result.restored_unique_tokens}"
        )
        if result.unmatched_surrogates:
            console.print(
                Text(
                    "Unrecognized placeholders: "
                    + ", ".join(result.unmatched_surrogates)
                )
            )


@main.command()
def demo():
    """Show exact local roundtrips using actual allocations, with no invented response IDs."""
    samples = [
        (
            "Patient John Doe received 50mg at St. Jude Hospital.",
            MaskMode.CONTENT_WORDS,
        ),
        (
            "삼성전자가 카카오와 150억원의 보안 인프라를 구축합니다.",
            MaskMode.CONTENT_WORDS,
        ),
        ("def 고객조회(고객번호):\n    return 고객번호 + 50", MaskMode.CODE_AWARE),
    ]
    for i, (text, mode) in enumerate(samples):
        session = vault.create(f"demo_{time.time_ns()}_{i}", mode)
        try:
            masked = engine.mask_text(
                text,
                session.session_id,
                session.generator,
                session.forward_store,
                session.reverse_store,
                mode=mode,
            )
            restored = engine.unmask(
                masked.masked_text, session.session_id, session.reverse_store
            )
            assert restored.unmasked_text == text
            console.print(Panel(Text(masked.masked_text), title="Masked payload"))
            console.print(
                Panel(Text(restored.unmasked_text), title="Local restoration")
            )
        finally:
            vault.clear_session(session.session_id)


@main.command()
def benchmark():
    """Measure this machine's throughput; masking ratio is not a privacy guarantee."""
    text = "The engineers at Microsoft protect patient records with SecureMCP. " * 50
    session = vault.create(f"bench_{time.time_ns()}")
    try:
        started = time.perf_counter()
        masked = engine.mask_text(
            text,
            session.session_id,
            session.generator,
            session.forward_store,
            session.reverse_store,
        )
        elapsed = time.perf_counter() - started
        restored = engine.unmask(
            masked.masked_text, session.session_id, session.reverse_store
        )
        console.print(
            f"Benchmark Results: {len(text.split()) / max(elapsed, 0.0001):,.0f} words/sec"
        )
        console.print(
            "100.0% (Zero Divergence)"
            if restored.unmasked_text == text
            else "Discrepancy Detected"
        )
    finally:
        vault.clear_session(session.session_id)


if __name__ == "__main__":
    main()
