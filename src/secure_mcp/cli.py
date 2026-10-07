"""Trusted local CLI with opt-in encrypted session transfer."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import json
import os
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
    agent_state_dir,
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
@click.version_option(version="0.5.1")
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
    command = click.option(
        "--agent", type=click.Choice(["claude", "codex"]), default="claude"
    )(command)
    command = click.option("--global", "global_scope", is_flag=True)(command)
    return click.option(
        "--state-dir", type=click.Path(path_type=Path), default=default_state_dir
    )(command)


def _hook_settings(global_scope, agent="claude"):
    if agent == "codex":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        return (home if global_scope else Path.cwd() / ".codex") / "hooks.json"
    return (
        Path.home() / ".claude" / "settings.json"
        if global_scope
        else Path.cwd() / ".claude" / "settings.local.json"
    )


@main.command("init")
@_hook_options
def init_hooks(agent, global_scope, state_dir):
    """Install local agent tool hooks. Prompts/attachments are not intercepted."""
    try:
        backup = configure(
            _hook_settings(global_scope, agent),
            agent_state_dir(state_dir, agent),
            install=True,
            agent=agent,
        )
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        f"{agent} tool hooks installed. Restart the agent; run secure-mcp doctor --agent {agent}."
    )
    click.echo(
        "Scope: tool text only. Direct prompts, attachments and telemetry are not covered."
    )
    if backup:
        click.echo(f"Settings backup: {backup}")
    if agent == "codex":
        click.echo(
            "Codex: review/trust these hooks using /hooks. Screen restoration is not available."
        )


@main.command("uninstall")
@_hook_options
def uninstall_hooks(agent, global_scope, state_dir):
    """Remove only SecureMCP hooks; preserve other settings and encrypted state."""
    try:
        configure(
            _hook_settings(global_scope, agent),
            agent_state_dir(state_dir, agent),
            install=False,
            agent=agent,
        )
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        "SecureMCP hooks removed. Restart the agent. Encrypted state is retained."
    )


@main.command()
@_hook_options
def doctor(agent, global_scope, state_dir):
    """Inspect hook registration. Host-version compatibility needs a real smoke test."""
    try:
        status = installation_status(
            _hook_settings(global_scope, agent),
            agent_state_dir(state_dir, agent),
            agent,
        )
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(status, ensure_ascii=False))
    if not status["ready"]:
        raise click.ClickException("Incomplete hook installation.")


@main.command()
@click.option("--agent", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--state-dir", type=click.Path(path_type=Path), default=default_state_dir)
@click.option("--session-id", required=True)
def stats(agent, state_dir, session_id):
    """Show local hook session counters, without original values or mappings."""
    from secure_mcp.hooks import HookStore

    try:
        with HookStore(agent_state_dir(state_dir, agent)).session(
            session_id
        ) as session:
            click.echo(session.get_stats().model_dump_json() if session else "{}")
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@main.command(hidden=True)
@click.option("--agent", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--state-dir", type=click.Path(path_type=Path), required=True)
def hook(agent, state_dir):
    """JSON stdin/stdout entry point invoked by the local agent, never by an MCP model."""
    payload = None
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Expected a hook object.")
        result = handle_hook(payload, state_dir, agent)
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
        elif (
            agent == "codex"
            and isinstance(payload, dict)
            and payload.get("hook_event_name") == "PostToolUse"
        ):
            # Codex can replace a failed transformation with sanitized feedback.
            click.echo(
                json.dumps(
                    {
                        "continue": False,
                        "stopReason": "SecureMCP could not mask this tool result; original output withheld.",
                    }
                )
            )
        else:
            click.echo(
                "SecureMCP hook failed; original host output may be used.", err=True
            )
            raise click.exceptions.Exit(1)


@main.command("exec", context_settings={"ignore_unknown_options": True})
@click.option("--agent", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--state-dir", type=click.Path(path_type=Path), default=default_state_dir)
@click.option("--session-id", required=True)
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
def exec_masked(agent, state_dir, session_id, command):
    """Run executable + arguments locally; return only masked stdout/stderr (no implicit shell)."""
    from secure_mcp.hooks import HookStore

    store = HookStore(agent_state_dir(state_dir, agent))
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


@main.command("restore")
@click.option("--agent", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--state-dir", type=click.Path(path_type=Path), default=default_state_dir)
@click.option("--session-id", required=True)
def restore_hook_text(agent, state_dir, session_id):
    """Restore UTF-8 stdin using a local hook session, for user display only."""
    from secure_mcp.hooks import HookStore

    store = HookStore(agent_state_dir(state_dir, agent))
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
        with store.session(session_id) as session:
            restored = store.restore(text, session)
    except (ValueError, OSError):
        raise click.ClickException(
            "Local restoration failed; check the session and placeholders."
        ) from None
    click.echo(restored, nl=False)


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


@main.group()
def gateway():
    """Automatic masking/restoration using existing CLI subscription logins."""


@gateway.command("install")
@click.option("--agent", type=click.Choice(["claude", "codex", "both"]), default="both")
@click.option("--config", type=click.Path(path_type=Path))
@click.option("--port", type=click.IntRange(1024, 65535), default=38117)
def gateway_install(agent, config, port):
    """One-time user-wide setup. Keeps auth caches; installs automatic startup."""
    from secure_mcp.gateway_install import (
        default_config,
        ensure_gateway,
        install_gateway,
    )

    path = config or default_config()
    try:
        changed = install_gateway(path, agent, port)
        ensure_gateway(path)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        "Subscription gateway configured with user-login auto-start. Restart your CLI; no agent hooks were installed."
    )
    for file in changed:
        click.echo(str(file))


@gateway.command("ensure", hidden=True)
@click.option("--config", type=click.Path(path_type=Path), required=True)
def gateway_ensure(config):
    from secure_mcp.gateway_install import ensure_gateway

    try:
        ensure_gateway(config)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@gateway.command("run")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_run(config):
    """Run the subscription gateway in the foreground (normally auto-started)."""
    from secure_mcp.gateway import PrivacyGateway
    from secure_mcp.gateway_install import default_config, load_config

    try:
        settings = load_config(config or default_config())
        if settings.get("mode", "subscription") != "subscription":
            raise ValueError("Use gateway api-run for API configuration.")
        state = (config or default_config()).resolve().parent / "sessions"
        with PrivacyGateway(
            ("127.0.0.1", settings["port"]), settings["token"], state_dir=state
        ) as server:
            server.serve_forever()
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@gateway.command("uninstall")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_uninstall(config):
    """Restore backed-up settings; refuses to overwrite subsequent user edits."""
    from secure_mcp.gateway_install import default_config, uninstall_gateway

    try:
        uninstall_gateway(config or default_config())
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Previous agent settings restored. Restart the CLI.")


@gateway.command("status")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_status(config):
    """Check the authenticated local gateway without contacting a provider."""
    from secure_mcp.gateway_install import default_config, healthy, load_config

    try:
        ready = healthy(load_config(config or default_config()))
    except (ValueError, OSError):
        ready = False
    click.echo(
        "Subscription gateway ready"
        if ready
        else "Subscription gateway stopped or unconfigured"
    )
    if not ready:
        raise click.exceptions.Exit(1)


@gateway.command("stop")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_stop(config):
    """Stop the local daemon and release mappings; login startup can restart it."""
    from secure_mcp.gateway_install import default_config, stop_gateway

    try:
        stop_gateway(config or default_config())
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Gateway stopped.")


@gateway.command("api-install")
@click.option("--provider", type=click.Choice(["xai"]), required=True)
@click.option("--model", required=True, help="xAI model ID; API charges apply.")
@click.option("--config", type=click.Path(path_type=Path))
@click.option("--grok-home", type=click.Path(file_okay=False, path_type=Path))
@click.option("--port", type=click.IntRange(1024, 65535), default=38118)
def gateway_api_install(provider, model, config, grok_home, port):
    """Opt into separately billed xAI API mode and add a Grok custom model."""
    from secure_mcp.xai_install import default_api_config, install_api

    try:
        install_api(config or default_api_config(), model, port, grok_home)
    except (ValueError, OSError, TypeError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(
        "xAI API mode configured. API charges apply; browser subscription login is not used."
    )
    click.echo(
        "Run gateway api-run, then select grok -m secure_mcp_xai with XAI_API_KEY in its environment."
    )


@gateway.command("api-run")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_api_run(config):
    """Run the isolated xAI API gateway in the foreground; Ctrl+C stops it."""
    from secure_mcp.gateway import API_UPSTREAMS, PrivacyGateway
    from secure_mcp.xai_install import default_api_config, load_api_config

    path = config or default_api_config()
    try:
        settings = load_api_config(path)
        with PrivacyGateway(
            ("127.0.0.1", settings["port"]),
            settings["token"],
            upstreams={},
            api_upstreams={"xai": API_UPSTREAMS["xai"]},
            state_dir=path.resolve().parent / "sessions",
        ) as server:
            server.serve_forever()
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None


@gateway.command("api-uninstall")
@click.option("--config", type=click.Path(path_type=Path))
def gateway_api_uninstall(config):
    """Stop API mode and restore the backed-up Grok settings, preserving logins."""
    from secure_mcp.gateway_install import uninstall_gateway
    from secure_mcp.xai_install import default_api_config, load_api_config

    path = config or default_api_config()
    try:
        load_api_config(path)
        uninstall_gateway(path)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Grok settings restored. API gateway stopped.")


if __name__ == "__main__":
    main()
