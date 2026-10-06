"""Explicit opt-in subscription proof; normal CI never contacts model providers.

Set SECURE_MCP_LIVE_SUBSCRIPTION=1 and the relevant native CLI binary path.
Uses existing CLI-owned logins, temporary fixture code and no host settings changes.
"""

import json
from hashlib import sha256
import os
import subprocess
from threading import Thread
import urllib.error
import urllib.request

import pytest

from secure_mcp.gateway import GatewaySession, PrivacyGateway


@pytest.mark.parametrize("language", ["english", "korean"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_live_subscription_read_mask_and_restore(
    tmp_path, monkeypatch, agent, language
):
    if os.environ.get("SECURE_MCP_LIVE_SUBSCRIPTION") != "1":
        pytest.skip("Real subscription tests require explicit opt-in")
    binary = os.environ.get(f"SECURE_MCP_{agent.upper()}_BINARY")
    if not binary:
        pytest.skip("Native CLI not configured")
    function = "privateFunction" if language == "english" else "고객조회"
    parameter = "privateValue" if language == "english" else "고객정보"
    filename = "fixture.py" if language == "english" else "고객코드.py"
    email = "alice@example.com"
    source = f'def {function}({parameter}):\n    return "{email}" # private comment\n'
    (tmp_path / filename).write_text(source, encoding="utf-8")
    prompt = (
        f"Read `{filename}`. Show the code of `{function}`."
        if language == "english"
        else f"파일 `{filename}` 읽어줘. 함수 `{function}` 코드를 보여줘."
    )
    captured = []
    errors = []
    reasoning = []
    walk = GatewaySession.walk

    def record_reasoning(session, value, restore=False, code=False):
        if isinstance(value, dict) and value.get("type") == "reasoning":
            reasoning.append(
                (
                    restore,
                    {
                        key: (
                            sha256(item.encode()).hexdigest()[:12]
                            if key == "encrypted_content" and isinstance(item, str)
                            else len(item)
                            if isinstance(item, (str, list))
                            else type(item).__name__
                        )
                        for key, item in value.items()
                    },
                )
            )
        return walk(session, value, restore, code)

    monkeypatch.setattr(GatewaySession, "walk", record_reasoning)
    original = GatewaySession.request

    def capture(session, payload):
        try:
            masked = original(session, payload)
        except Exception as exc:
            errors.append(("request", type(exc).__name__, str(exc)))
            raise
        captured.append(json.dumps(masked, ensure_ascii=False))
        return masked

    monkeypatch.setattr(GatewaySession, "request", capture)
    original_response = GatewaySession.response

    def capture_response(session, body, content_type):
        try:
            return original_response(session, body, content_type)
        except Exception as exc:
            errors.append(("response", type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(GatewaySession, "response", capture_response)
    open_request = urllib.request.OpenerDirector.open

    def capture_error(opener, request, *args, **kwargs):
        try:
            return open_request(opener, request, *args, **kwargs)
        except urllib.error.HTTPError as exc:
            if "/v1/messages" in request.full_url or "/responses" in request.full_url:
                try:
                    error = json.loads(exc.read(4096)).get("error", {})
                    errors.append(
                        (exc.code, error.get("type"), error.get("message", "")[:500])
                    )
                except ValueError:
                    errors.append((exc.code, "non-json"))
            raise

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", capture_error)
    gateway = PrivacyGateway(("127.0.0.1", 0), "live-fixture-local-token")
    Thread(target=gateway.serve_forever, daemon=True).start()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ORCA_")
        and key
        not in {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
        }
        and (not key.startswith("CODEX_") or key == "CODEX_HOME")
    }
    if agent == "claude":
        env.update(
            {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{gateway.server_port}/claude",
                "ANTHROPIC_CUSTOM_HEADERS": "X-SecureMCP-Token: live-fixture-local-token",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_MAX_RETRIES": "0",
            }
        )
        command = [
            binary,
            "--print",
            "--safe-mode",
            "--setting-sources",
            "",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--model",
            "sonnet",
            "--max-turns",
            "5",
        ]
    else:
        provider = "model_providers.secure_mcp_subscription."
        configs = {
            "model_provider": "secure_mcp_subscription",
            "model": "gpt-6.1-sol",
            "forced_login_method": "chatgpt",
            provider + "name": "SecureMCP subscription",
            provider + "base_url": f"http://127.0.0.1:{gateway.server_port}/codex",
            provider + "wire_api": "responses",
            provider + "requires_openai_auth": True,
            provider + "supports_websockets": False,
            provider + "request_max_retries": 0,
            provider + "stream_max_retries": 0,
            provider + "http_headers.X-SecureMCP-Token": "live-fixture-local-token",
        }
        for feature in [
            "hooks",
            "apps",
            "plugins",
            "multi_agent",
            "image_generation",
            "browser_use",
            "computer_use",
        ]:
            configs["features." + feature] = False
        command = [
            binary,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "danger-full-access",
            "-C",
            str(tmp_path),
        ]
        for key, value in configs.items():
            command.extend(["-c", key + "=" + json.dumps(value)])
        command.append("-")
    try:
        result = subprocess.run(
            command,
            input=prompt,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode:
            print(json.dumps({"errors": errors, "reasoning_shapes": reasoning}))
        assert result.returncode == 0, (
            errors + reasoning,
            result.stdout[-1500:],
            result.stderr[-1000:],
        )
        assert function in result.stdout and email in result.stdout, result.stdout[
            -1500:
        ]
        assert len(captured) >= 2, "No file-reading inference round trip"
        assert all(
            function not in payload
            and email not in payload
            and parameter not in payload
            and "private comment" not in payload
            for payload in captured
        )
        # Restore the captured read result using the live mapping table, not a provider stub.
        assert any(
            all(
                marker in gateway.sessions[agent].restore(payload)
                for marker in [function, parameter, email]
            )
            for payload in captured
        ), (
            "The actual file content did not reach a masked follow-up request",
            [
                {
                    "markers": [marker in gateway.sessions[agent].restore(payload) for marker in [function, parameter, email]],
                    "fixture": gateway.sessions[agent].restore(payload)[max(0, gateway.sessions[agent].restore(payload).find(email) - 250):gateway.sessions[agent].restore(payload).find(email) + 100] if email in gateway.sessions[agent].restore(payload) else "",
                }
                for payload in captured
            ],
        )
        assert (tmp_path / filename).read_text(encoding="utf-8") == source
    finally:
        gateway.shutdown()
        gateway.server_close()
