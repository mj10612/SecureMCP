import json
import re
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.request

import pytest
import tomlkit

from secure_mcp.gateway import GatewaySession, PrivacyGateway
from secure_mcp.gateway_install import (
    install_gateway,
    uninstall_gateway,
    healthy,
    ensure_gateway,
    stop_gateway,
)


def test_claude_attribution_stays_first_without_exposing_user_context():
    from secure_mcp.gateway import PUBLIC_CLAUDE_IDENTITIES, PRIVACY_INSTRUCTION

    session = GatewaySession()
    attribution = {
        "type": "text",
        "text": "x-anthropic-billing-header: cc_version=2.1.287.63f; cc_entrypoint=sdk-cli;",
        "cache_control": {"type": "ephemeral"},
    }
    payload = {
        "model": "fixture",
        "system": [
            attribution,
            {"type": "text", "text": PUBLIC_CLAUDE_IDENTITIES[1]},
            {"type": "text", "text": "PrivateCompany alice@example.com"},
        ],
        "messages": [
            {
                "role": "user",
                "content": "Review A\n```python\ndef A(secret):\n    return secret + 42\n```",
            }
        ],
    }
    masked = session.request(payload)
    assert masked["system"][0] == attribution
    assert masked["system"][1]["text"] == PUBLIC_CLAUDE_IDENTITIES[1]
    assert masked["system"][-1]["text"] == PRIVACY_INSTRUCTION
    assert all(
        secret not in json.dumps(masked)
        for secret in ["PrivateCompany", "alice@example.com", "def A(", "secret + 42"]
    )
    assert "x-anthropic-billing-header" not in session.mask(attribution["text"])


@pytest.mark.parametrize(
    "text",
    [
        "x-anthropic-billing-header: cc_version=2.1.287.63f; cc_entrypoint=sdk-cli;\nprivateCompany secret",
        "x-anthropic-billing-header: cc_version=2.1.287; cc_entrypoint=privateCompany;",
        "x-anthropic-billing-header: cc_version=2.1.287; cc_entrypoint=cli; secret=123;",
    ],
)
def test_claude_attribution_cannot_hide_private_text(text):
    with pytest.raises(ValueError, match="attribution"):
        GatewaySession().request(
            {"messages": [], "system": [{"type": "text", "text": text}]}
        )


def test_claude_attribution_cannot_be_moved_or_merged():
    block = {
        "type": "text",
        "text": "x-anthropic-billing-header: cc_version=2.1.287; cc_entrypoint=cli;",
    }
    session = GatewaySession()
    with pytest.raises(ValueError, match="attribution"):
        session.request(
            {
                "messages": [],
                "system": [{"type": "text", "text": "private context"}, block],
            }
        )
    with pytest.raises(ValueError, match="attribution"):
        session.request({"messages": [], "system": block["text"]})


def test_claude_public_identity_in_source_is_still_masked():
    from secure_mcp.gateway import PUBLIC_CLAUDE_IDENTITIES

    session = GatewaySession()
    code = "identity = " + repr(PUBLIC_CLAUDE_IDENTITIES[1])
    assert PUBLIC_CLAUDE_IDENTITIES[1] not in session.mask(code, code=True)


def test_shared_prompt_code_and_korean_roundtrip():
    session = GatewaySession()
    payload = {
        "model": "fixture",
        "input": [
            {"role": "user", "content": "Review A and 고객조회."},
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "def A(고객조회):\n    return 고객조회 + 123 # alice@example.com",
            },
        ],
        "instructions": "Inspect the source.",
    }
    masked = session.request(payload)
    prompt = masked["input"][0]["content"]
    source = masked["input"][1]["output"]
    a = session.session.forward_store["code:identifier:A"].surrogate
    korean = session.session.forward_store["code:identifier:고객조회"].surrogate
    assert a in prompt and a in source
    assert korean in prompt and korean in source
    assert "alice@example.com" not in json.dumps(masked)
    assert "123" not in source
    assert session.restore(source) == payload["input"][1]["output"]
    assert session.restore(prompt) == payload["input"][0]["content"]


def test_early_prompt_name_matches_later_code():
    session = GatewaySession()
    prompt = session.mask("A 함수를 봐줘")
    code = session.mask("def A(customer):\n return customer", code=True)
    assert session.session.forward_store["code:identifier:A"].surrogate in prompt
    assert session.session.forward_store["code:identifier:A"].surrogate in code
    assert session.restore(prompt) == "A 함수를 봐줘"


@pytest.mark.parametrize(
    "source",
    [
        "def 결제(계좌):\n    return 계좌 + 42 # 비밀 설명",
        'function privateFunction(secret) { return "private-value" + secret + 42; }',
        "SELECT secret_column FROM private_table WHERE secret_column = 42",
    ],
)
def test_code_all_identifiers_literals_and_comments(source):
    session = GatewaySession()
    masked = session.mask(source, code=True)
    assert session.restore(masked) == source
    for marker in [
        "결제",
        "계좌",
        "비밀",
        "privateFunction",
        "secret",
        "private-value",
        "42",
        "secret_column",
        "private_table",
    ]:
        if marker in source:
            assert not re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", masked)


def sse(events):
    return "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events
    ).encode()


def parse_sse(body):
    return [
        json.loads(line[6:])
        for line in body.decode().splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_split_stream_and_json_arguments_are_restored(agent):
    session = GatewaySession()
    alias = session.mask("confidentialFunction", code=True)
    if agent == "codex":
        events = [
            {"type": "response.output_text.delta", "item_id": "m1", "delta": alias[:4]},
            {"type": "response.output_text.delta", "item_id": "m1", "delta": alias[4:]},
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "f1",
                "delta": '{"cmd":"' + alias[:4],
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "f1",
                "delta": alias[4:] + '"}',
            },
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": alias}],
                        }
                    ]
                },
            },
        ]
    else:
        events = [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": alias[:4]},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": alias[4:]},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"file_path":"' + alias[:4],
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": alias[4:] + '"}'},
            },
        ]
    restored = parse_sse(session.response(sse(events), "text/event-stream"))
    assert "confidentialFunction" in json.dumps(restored)
    assert alias not in json.dumps(restored)
    if agent == "claude":
        assert json.loads(restored[2]["delta"]["partial_json"]) == {
            "file_path": "confidentialFunction"
        }
    else:
        assert json.loads(restored[2]["delta"]) == {"cmd": "confidentialFunction"}


def test_signed_thinking_stays_masked_and_can_be_replayed():
    session = GatewaySession()
    alias = session.mask("privateThing", code=True)
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": alias},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "signed"},
        },
    ]
    body = session.response(sse(events), "text/event-stream")
    assert alias.encode() in body and b"privateThing" not in body
    block = {"type": "thinking", "thinking": alias, "signature": "signed"}
    assert session.walk(block) == block
    with pytest.raises(ValueError):
        session.walk({**block, "thinking": "raw-secret"})


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "x", "input": [{"type": "input_image", "image_url": "raw-secret"}]},
        {"model": "x", "input": [], "previous_response_id": "r1"},
        {"model": "x", "input": [], "private_field": "raw-secret"},
        {"model": "x", "input": [{"type": "input_text", "signature": "raw-secret"}]},
        {"model": "x", "input": [], "background": True},
    ],
)
def test_unsupported_payloads_fail_closed(payload):
    with pytest.raises(ValueError):
        GatewaySession().request(payload)


def test_unknown_response_alias_refused():
    with pytest.raises(ValueError):
        GatewaySession().response(
            b'{"content":[{"type":"text","text":"smcp_ID_999"}]}', "application/json"
        )


def test_gateway_install_preserves_settings_login_and_uninstalls(tmp_path):
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    cs = claude / "settings.json"
    ct = codex / "config.toml"
    cs.write_text(
        '{"permissions":{"allow":["Read"]},"env":{"OTHER":"keep"}}', encoding="utf-8"
    )
    ct.write_text(
        '# keep comment\nmodel = "existing-model"\nmodel_provider = "openai"\n',
        encoding="utf-8",
    )
    auth = codex / "auth.json"
    auth.write_text("LOGIN-CACHE-UNCHANGED", encoding="utf-8")
    before = cs.read_bytes(), ct.read_bytes()
    config = tmp_path / "state" / "config.json"
    startup = tmp_path / "startup.vbs"
    install_gateway(config, claude_dir=claude, codex_dir=codex, startup_path=startup)
    settings = json.loads(cs.read_text())
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["env"]["OTHER"] == "keep"
    assert "ANTHROPIC_API_KEY" not in settings["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in settings["env"]
    value = tomlkit.parse(ct.read_text())
    assert value["model"] == "existing-model"
    assert value["model_providers"]["secure_mcp_subscription"]["requires_openai_auth"]
    assert value["forced_login_method"] == "chatgpt"
    assert auth.read_text() == "LOGIN-CACHE-UNCHANGED"
    install_gateway(config, claude_dir=claude, codex_dir=codex, startup_path=startup)
    assert not json.loads(cs.read_text()).get("hooks")
    assert startup.is_file()
    uninstall_gateway(config)
    assert (cs.read_bytes(), ct.read_bytes()) == before
    assert not (codex / "hooks.json").exists()
    assert not startup.exists()


def test_install_refuses_paid_override_without_modifying_hosts(tmp_path):
    claude = tmp_path / "claude"
    claude.mkdir()
    settings = claude / "settings.json"
    settings.write_text('{"env":{"ANTHROPIC_API_KEY":"dont-print-this"}}')
    before = settings.read_bytes()
    with pytest.raises(ValueError):
        install_gateway(
            tmp_path / "state/config.json",
            agent="claude",
            claude_dir=claude,
            startup_path=tmp_path / "startup.vbs",
        )
    assert settings.read_bytes() == before


def test_uninstall_refuses_subsequent_edits(tmp_path):
    config = tmp_path / "state/config.json"
    claude = tmp_path / "claude"
    install_gateway(
        config, agent="claude", claude_dir=claude, startup_path=tmp_path / "startup.vbs"
    )
    settings = claude / "settings.json"
    settings.write_text("{}")
    with pytest.raises(ValueError):
        uninstall_gateway(config)
    assert settings.read_text() == "{}"


class EchoProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload, dict(self.headers)))
        value = payload.get("messages", payload.get("input"))[0]["content"]
        result = {"content": [{"type": "text", "text": value}]}
        body = json.dumps(result).encode()
        if payload.get("stream"):
            body = (
                "event: response.output_text.delta\ndata: "
                + json.dumps(
                    {
                        "type": "response.output_text.delta",
                        "delta": value,
                    }
                )
                + "\n\n"
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("anthropic-ratelimit-unified-status", "allowed")
        self.end_headers()
        self.wfile.write(body)


def test_uninstalled_config_can_be_reinstalled_with_a_new_port(tmp_path):
    config = tmp_path / "state" / "config.json"
    claude = tmp_path / "claude"
    startup = tmp_path / "startup.vbs"
    install_gateway(
        config, agent="claude", port=38117, claude_dir=claude, startup_path=startup
    )
    uninstall_gateway(config)
    assert config.exists()
    install_gateway(
        config, agent="claude", port=40000, claude_dir=claude, startup_path=startup
    )
    assert json.loads(config.read_text())["port"] == 40000
    # An installed gateway still refuses an implicit port change.
    with pytest.raises(ValueError, match="another port"):
        install_gateway(
            config, agent="claude", port=41000, claude_dir=claude, startup_path=startup
        )
    uninstall_gateway(config)


def test_http_proxy_oauth_no_api_keys_or_raw_upstream(tmp_path):
    provider = ThreadingHTTPServer(("127.0.0.1", 0), EchoProvider)
    provider.requests = []
    Thread(target=provider.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{provider.server_port}"
    gateway = PrivacyGateway(
        ("127.0.0.1", 0), "local-token", {"claude": upstream, "codex": upstream}
    )
    Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        assert healthy({"port": gateway.server_port, "token": "local-token"})
        for agent, path, auth in [
            ("claude", "/claude/v1/messages", "Bearer sk-ant-oat01-offline"),
            ("codex", "/codex/responses", "Bearer eyJoffline.subscription"),
        ]:
            raw = "Review A and 고객조회 at alice@example.com"
            field = "messages" if agent == "claude" else "input"
            body = json.dumps(
                {"model": "fixture", field: [{"role": "user", "content": raw}]}
            ).encode()
            headers = {
                "X-SecureMCP-Token": "local-token",
                "Authorization": auth,
                "Content-Type": "application/json",
                "anthropic-beta": "oauth-capability",
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{gateway.server_port}{path}", body, headers
            )
            with urllib.request.urlopen(request) as response:
                assert json.load(response)["content"][0]["text"] == raw
                assert (
                    response.headers["anthropic-ratelimit-unified-status"] == "allowed"
                )
            if agent == "codex":
                stream_body = json.dumps(
                    {
                        "model": "fixture",
                        "input": [{"role": "user", "content": raw}],
                        "stream": True,
                    }
                ).encode()
                stream_request = urllib.request.Request(
                    request.full_url, stream_body, headers
                )
                with urllib.request.urlopen(stream_request) as response:
                    assert response.headers["Content-Type"] == "text/event-stream"
                    assert raw in response.read().decode()
            assert raw not in json.dumps(provider.requests[-1][1])
            assert "alice@example.com" not in json.dumps(provider.requests[-1][1])
            assert provider.requests[-1][2]["Authorization"] == auth
            assert "X-Securemcp-Token" not in provider.requests[-1][2]
            before = len(provider.requests)
            request.headers["Authorization"] = "Bearer sk-paid-api-key"
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(request)
            assert exc.value.code == 401
            exc.value.close()
            assert len(provider.requests) == before
    finally:
        gateway.shutdown()
        gateway.server_close()
        provider.shutdown()
        provider.server_close()


def test_loopback_only():
    with pytest.raises(ValueError):
        PrivacyGateway(("0.0.0.0", 0), "token")


def test_encrypted_restart_preserves_shared_aliases_and_signed_reasoning(tmp_path):
    state = tmp_path / "sessions"
    gateway = PrivacyGateway(("127.0.0.1", 0), "password-local-token", state_dir=state)
    session = gateway.get_session("codex")
    alias = session.mask("privateFunction", code=True)
    block = {
        "type": "reasoning",
        "id": "r1",
        "summary": [],
        "content": [],
        "encrypted_content": "ciphertext",
    }
    session.walk(block, restore=True)
    gateway.save("codex", session)
    gateway.server_close()
    assert b"privateFunction" not in (state / "codex-symbols-v2.enc").read_bytes()
    restarted = PrivacyGateway(
        ("127.0.0.1", 0), "password-local-token", state_dir=state
    )
    try:
        loaded = restarted.get_session("codex")
        assert loaded.mask("privateFunction", code=True) == alias
        assert loaded.mask("secondPrivate", code=True) != alias
        assert loaded.restore(alias) == "privateFunction"
        assert (
            loaded.walk({**block, "status": "completed"})["encrypted_content"]
            == "ciphertext"
        )
        replay = {key: value for key, value in block.items() if key != "content"}
        assert loaded.walk(replay)["encrypted_content"] == "ciphertext"
        for changed in (
            {**replay, "encrypted_content": "changed"},
            {**replay, "content": [{"type": "text", "text": "injected"}]},
        ):
            with pytest.raises(ValueError, match="Unrecognized"):
                loaded.walk(changed)
    finally:
        restarted.server_close()


def test_neutral_aliases_are_strict_and_do_not_collide_with_source_names():
    session = GatewaySession()
    alias = session.mask("private_symbol_1", code=True)
    assert alias.startswith("private_symbol_") and alias != "private_symbol_1"
    assert session.restore(alias) == "private_symbol_1"
    with pytest.raises(ValueError, match="surrogate"):
        session.restore("private_symbol_999999")


def test_legacy_snapshots_remain_readable_and_are_not_rewritten(tmp_path):
    from secure_mcp.encrypted_session import load_session, save_session
    from secure_mcp.engine.strategies import mapping_key
    from secure_mcp.models import SurrogateStrategy, TokenMapping, TokenType
    from secure_mcp.session import PrivacySession

    state = tmp_path / "sessions"
    state.mkdir()
    legacy = PrivacySession("gateway", strategy=SurrogateStrategy.UNICODE)
    mapping = TokenMapping(
        original="privateFunction",
        surrogate="smcp_ID_42",
        token_type=TokenType.IDENTIFIER,
        context="code",
    )
    legacy.forward_store[
        mapping_key(mapping.original, mapping.token_type, code=True)
    ] = mapping
    legacy.reverse_store[mapping.surrogate] = mapping
    old = state / "codex.enc"
    password = "local-migration-password"
    save_session(old, legacy, password)
    previous = old.read_bytes()
    gateway = PrivacyGateway(("127.0.0.1", 0), password, state_dir=state)
    try:
        current = gateway.get_session("codex")
        assert current.mask("privateFunction", code=True).startswith("private_symbol_")
        gateway.save("codex", current)
        assert old.read_bytes() == previous
        assert (state / "codex-symbols-v2.enc").exists()
        loaded = GatewaySession(load_session(old, password, "gateway"))
        assert loaded.restore("smcp_ID_42") == "privateFunction"
    finally:
        gateway.server_close()


def test_tool_schema_dialect_is_public_protocol_and_private_uris_are_masked():
    session = GatewaySession()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"privateField": {"type": "string"}},
    }
    masked = session.walk(schema)
    assert masked["$schema"] == schema["$schema"]
    assert "privateField" not in json.dumps(masked)
    private = session.walk({"$schema": "https://privateCompany.example/schema"})
    assert private["$schema"] != "https://privateCompany.example/schema"
    assert "privateCompany" not in json.dumps(private)
    code = 'uri = "https://json-schema.org/draft/2020-12/schema"'
    assert schema["$schema"] not in session.mask(code, code=True)


@pytest.mark.parametrize(
    "dialect",
    [
        "https://json-schema.org/schema",
        "http://json-schema.org/draft-03/schema#",
        "https://spec.openapis.org/oas/3.1/dialect/base",
    ],
)
def test_additional_public_schema_dialects_pass_through(dialect):
    session = GatewaySession()
    masked = session.request(
        {
            "model": "fixture",
            "input": [],
            "tools": [
                {
                    "type": "function",
                    "name": "t",
                    "parameters": {"$schema": dialect, "type": "object"},
                }
            ],
        }
    )
    assert dialect in json.dumps(masked)


def test_non_string_schema_values_do_not_raise_type_errors():
    session = GatewaySession()
    masked = session.walk(
        {"$schema": {"type": "object"}, "title": "confidentialSchema body"}
    )
    assert isinstance(masked["$schema"], dict)
    assert "confidentialSchema" not in json.dumps(masked)
    assert session.walk({"$schema": [1, 2]}) == {"$schema": [1, 2]}


def test_tool_result_with_data_field_is_masked_not_rejected():
    session = GatewaySession()
    masked = session.walk(
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"data": "customer-secret", "note": "keep"},
        }
    )
    assert "customer-secret" not in json.dumps(masked)


def test_redacted_thinking_is_opaque_and_replayable():
    session = GatewaySession()
    block = {"type": "redacted_thinking", "data": "ENCRYPTED-OPAQUE"}
    assert session.walk(block, restore=True) == block
    assert session.walk(block) == block
    with pytest.raises(ValueError, match="reasoning"):
        GatewaySession().walk(block)
    with pytest.raises(ValueError, match="Binary/remote"):
        GatewaySession().walk(
            {"type": "image", "source": {"data": "x", "media_type": "image/png"}}
        )


def test_legacy_glued_numeric_alias_is_restored_in_gateway_tool_arguments():
    session = GatewaySession()
    first = session.mask("0.5", code=True)
    second = session.mask(".1", code=True)
    session.session.reverse_store[first].context = "code"
    # Simulate a legacy masked edit that glued two numeric aliases together.
    response = {
        "content": [
            {
                "type": "tool_use",
                "id": "t2",
                "name": "Edit",
                "input": {
                    "file_path": "pyproject.toml",
                    "new_string": f"version = {first}{second}",
                },
            }
        ]
    }
    restored = json.loads(
        session.response(json.dumps(response).encode(), "application/json")
    )
    assert restored["content"][0]["input"]["new_string"] == "version = 0.5.1"


def test_dotted_numbers_are_masked_as_one_token_and_restored():
    session = GatewaySession()
    masked = session.mask("version = 0.5.1", code=True)
    assert session.restore(masked) == "version = 0.5.1"
    for text in ("ip = 192.168.0.1", "release = 1.2.3"):
        masked = session.mask(text, code=True)
        assert session.restore(masked) == text


def test_inline_code_with_apostrophe_falls_back_to_text_masking():
    session = GatewaySession()
    for text in (
        "See `don't` here",
        "Check `user's_name` field",
        "```\ndon't panic now\n```",
    ):
        masked = session.mask(text)
        assert "don't" not in masked and "user's_name" not in masked
        assert session.restore(masked) == text


def test_inline_code_fallback_never_forwards_raw_content():
    session = GatewaySession()
    masked = session.mask("Check `secretCustomerName` value")
    assert "secretCustomerName" not in masked
    assert session.restore(masked) == "Check `secretCustomerName` value"


def test_auto_start_is_idempotent_and_stop_releases_daemon(tmp_path):
    import socket
    import time

    config = tmp_path / "config.json"
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    value = {"port": port, "token": "test-local-token-with-more-than-32-characters"}
    config.write_text(json.dumps(value), encoding="utf-8")
    try:
        ensure_gateway(config)
        ensure_gateway(config)
        assert healthy(value)
    finally:
        stop_gateway(config)
    for _ in range(30):
        if not healthy(value):
            break
        time.sleep(0.1)
    assert not healthy(value)


def test_custom_tool_name_and_schema_keys_are_consistent():
    session = GatewaySession()
    request = {
        "model": "fixture",
        "input": [],
        "tools": [
            {
                "type": "function",
                "name": "privateFunction",
                "parameters": {
                    "type": "object",
                    "properties": {"privateProperty": {"type": "string"}},
                    "required": ["privateProperty"],
                },
            }
        ],
    }
    masked = session.request(request)
    tool = masked["tools"][0]
    assert "privateFunction" not in json.dumps(masked)
    assert "privateProperty" not in json.dumps(masked)
    key = next(iter(tool["parameters"]["properties"]))
    assert tool["parameters"]["required"] == [key]
    call = {
        "type": "function_call",
        "name": tool["name"],
        "arguments": json.dumps({key: "public"}),
    }
    restored = session.walk(call, restore=True)
    assert restored["name"] == "privateFunction"
    assert json.loads(restored["arguments"]) == {"privateProperty": "public"}


def test_public_exec_grammar_is_preserved_and_private_grammar_is_refused():
    from secure_mcp.gateway import EXEC_GRAMMAR

    tool = {
        "type": "custom",
        "name": "exec",
        "format": {
            "type": "grammar",
            "syntax": "lark",
            "definition": EXEC_GRAMMAR,
        },
    }
    session = GatewaySession()
    masked = session.request(
        {"input": [{"type": "tool_namespace", "name": "functions", "tools": [tool]}]}
    )
    assert masked["input"][0]["tools"][0]["format"] == tool["format"]
    tool["format"]["definition"] += "\nprivateCompany: /secret/"
    with pytest.raises(ValueError, match="grammar"):
        session.walk(tool)


def test_safeguard_paths_are_masked_and_safety_protocol_is_preserved():
    session = GatewaySession()
    safeguards = [
        {
            "type": "dangerous_tool_use",
            "classifier_context": {
                "permission_mode": "auto",
                "platform": "windows",
                "live_cwd": "C:/PrivateCompany/SecretProject",
                "rules": {"deny": [{"rule": "Bash", "source": "toolsNarrowing"}]},
            },
        }
    ]
    result = session.request({"messages": [], "safeguards": safeguards})
    assert "PrivateCompany" not in json.dumps(result)
    context = result["safeguards"][0]["classifier_context"]
    assert context["permission_mode"] == "auto"
    assert context["rules"] == safeguards[0]["classifier_context"]["rules"]
    assert session.restore(context["live_cwd"]) == "C:/PrivateCompany/SecretProject"


def test_source_identifier_overrides_task_vocabulary_and_unfenced_code_is_lexed():
    session = GatewaySession()
    code = session.mask(
        'def review(secret):\n    return "private literal" + secret + 42'
    )
    assert "private literal" not in code and not re.search(r"\b42\b", code)
    alias = session.session.forward_store["code:identifier:review"].surrogate
    assert alias in session.mask("Please review review.")
    assert (
        session.restore(code)
        == 'def review(secret):\n    return "private literal" + secret + 42'
    )


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_startup_registration_formats(platform, monkeypatch, tmp_path):
    import plistlib
    from secure_mcp import gateway_install

    monkeypatch.setattr(gateway_install.sys, "platform", platform)
    args = [
        "C:/Program Files/Python/python.exe",
        "-m",
        "secure_mcp",
        "gateway",
        "ensure",
        "--config",
        "C:/private path/config.json",
    ]
    path, data = gateway_install.startup_registration(args, tmp_path / "startup")
    assert path == tmp_path / "startup"
    if platform == "win32":
        assert '""C:/Program Files/Python/python.exe""' in data.decode("utf-16")
        assert ", 0, False" in data.decode("utf-16")
    elif platform == "darwin":
        assert plistlib.loads(data)["ProgramArguments"] == args
        assert plistlib.loads(data)["RunAtLoad"]
    else:
        assert 'Exec="C:/Program Files/Python/python.exe"' in data.decode()
        assert "Terminal=false" in data.decode()


def test_windows_hidden_startup_launcher_executes_quoted_command(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from secure_mcp.gateway_install import startup_registration

    if os.name != "nt":
        pytest.skip("Native Windows startup launcher")
    marker = tmp_path / "directory with spaces" / "started.txt"
    marker.parent.mkdir()
    script = tmp_path / "startup.vbs"
    command = (
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')"
    )
    _, data = startup_registration([sys.executable, "-c", command, str(marker)], script)
    script.write_bytes(data)
    completed = subprocess.run(
        ["cscript.exe", "//B", "//Nologo", str(script)], capture_output=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr
    for _ in range(40):
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.read_text() == "started"


def test_install_rolls_back_partial_file_updates(monkeypatch, tmp_path):
    from secure_mcp import gateway_install

    claude = tmp_path / "claude"
    claude.mkdir()
    original = b'{"permissions":{"defaultMode":"default"}}\n'
    (claude / "settings.json").write_bytes(original)
    write = gateway_install.atomic_bytes

    def failing_write(path, data):
        if path.name == "settings.json":
            raise OSError("injected write failure")
        return write(path, data)

    monkeypatch.setattr(gateway_install, "atomic_bytes", failing_write)
    startup = tmp_path / "startup.vbs"
    config = tmp_path / "state" / "config.json"
    with pytest.raises(OSError, match="injected"):
        install_gateway(config, agent="claude", claude_dir=claude, startup_path=startup)
    assert (claude / "settings.json").read_bytes() == original
    assert not startup.exists()
    assert not (config.parent / "installation.json").exists()


def test_custom_tool_input_split_stream_roundtrip():
    session = GatewaySession()
    alias = session.mask("privateFunction", code=True)
    parts = [alias[:4], alias[4:]]
    body = "".join(
        "data: "
        + json.dumps(
            {
                "type": "response.custom_tool_call_input.delta",
                "item_id": "c1",
                "delta": part,
            }
        )
        + "\n\n"
        for part in parts
    ).encode()
    restored = session.response(body, "text/event-stream").decode()
    events = [json.loads(frame[6:]) for frame in restored.strip().split("\n\n")]
    assert "".join(event["delta"] for event in events) == "privateFunction"


def test_schema_references_follow_definition_aliases():
    session = GatewaySession()
    schema = {
        "type": "object",
        "$defs": {"privateSchema": {"type": "string"}},
        "properties": {"privateValue": {"$ref": "#/$defs/privateSchema"}},
    }
    masked = session.walk(schema)
    name = next(iter(masked["$defs"]))
    prop = next(iter(masked["properties"].values()))
    assert prop["$ref"] == "#/$defs/" + name
    assert session.walk(masked, restore=True) == schema


def test_schema_refs_through_combinators_and_indexes_stay_resolvable():
    session = GatewaySession()
    schema = {
        "type": "object",
        "properties": {
            "value": {"oneOf": [{"type": "string"}, {"type": "number"}]},
            "copy": {"$ref": "#/properties/value/oneOf/0"},
            "nested": {"anyOf": [{"properties": {"secretField": {"type": "integer"}}}]},
            "nestedCopy": {"$ref": "#/properties/nested/anyOf/0/properties/secretField"},
        },
    }
    masked = session.walk(schema)

    def resolve(document, pointer):
        node = document
        for segment in pointer[2:].split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            node = node[int(segment)] if isinstance(node, list) else node[segment]
        return node

    refs = [
        value["$ref"]
        for value in masked["properties"].values()
        if isinstance(value, dict) and "$ref" in value
    ]
    assert len(refs) == 2
    for ref in refs:
        # The local pointer must resolve inside the masked schema it ships with.
        assert resolve(masked, ref) is not None
    assert "secretField" not in json.dumps(masked)
    assert session.walk(masked, restore=True) == schema


def test_enum_and_const_values_are_masked_even_with_protocol_keys():
    session = GatewaySession()
    payload = {
        "messages": [],
        "tools": [
            {
                "name": "lookup",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "payload": {
                            "enum": [
                                {
                                    "id": "alice@example.com",
                                    "type": "private-company",
                                    "cache_control": {"secret": "customer-token"},
                                    "model": "internal-model-name",
                                }
                            ],
                            "const": {"role": "auditor", "status": "confidential"},
                        }
                    },
                },
            }
        ],
    }
    masked = session.request(payload)
    dumped = json.dumps(masked["tools"])
    for secret in [
        "alice@example.com",
        "private-company",
        "customer-token",
        "internal-model-name",
        "auditor",
        "confidential",
    ]:
        assert secret not in dumped
    assert session.walk(masked["tools"], restore=True) == payload["tools"]


def test_expired_gateway_session_is_not_revived_by_save():
    import time

    gateway = PrivacyGateway(("127.0.0.1", 0), "y" * 40)
    try:
        session = gateway.get_session("claude")
        alias = session.mask("ConfidentialCustomer")
        assert session.restore(alias) == "ConfidentialCustomer"
        session.session.last_accessed = time.time() - 90000
        assert session.session.is_expired()
        gateway.save("claude", session)
        replacement = gateway.get_session("claude")
        assert replacement is not session
        assert replacement.session.forward_store == {}
        with pytest.raises(ValueError):
            replacement.restore(alias)
    finally:
        gateway.server_close()


def test_expired_snapshot_is_discarded_without_losing_file(tmp_path):
    import time

    state = tmp_path / "sessions"
    state.mkdir()
    gateway = PrivacyGateway(("127.0.0.1", 0), "z" * 40, state_dir=state)
    try:
        session = gateway.get_session("codex")
        session.mask("privateFunction", code=True)
        session.session.last_accessed = time.time() - 90000
        gateway.save("codex", session)
        reopened = PrivacyGateway(("127.0.0.1", 0), "z" * 40, state_dir=state)
        try:
            fresh = reopened.get_session("codex")
            assert fresh.session.forward_store == {}
        finally:
            reopened.server_close()
    finally:
        gateway.server_close()

    # A healthy snapshot still reloads across restarts.
    healthy = PrivacyGateway(("127.0.0.1", 0), "w" * 40, state_dir=tmp_path / "ok")
    try:
        session = healthy.get_session("codex")
        alias = session.mask("privateFunction", code=True)
        healthy.save("codex", session)
    finally:
        healthy.server_close()
    restarted = PrivacyGateway(("127.0.0.1", 0), "w" * 40, state_dir=tmp_path / "ok")
    try:
        assert restarted.get_session("codex").restore(alias) == "privateFunction"
    finally:
        restarted.server_close()


def test_expired_load_session_raises_dedicated_error(tmp_path):
    from secure_mcp.encrypted_session import SessionExpiredError, save_session
    from secure_mcp.session import PrivacySession

    path = tmp_path / "expired.enc"
    session = PrivacySession("gateway", ttl_seconds=1)
    session.last_accessed -= 10
    save_session(path, session, "password")
    with pytest.raises(SessionExpiredError):
        from secure_mcp.encrypted_session import load_session

        load_session(path, "password", "gateway")


def test_sse_unicode_line_separator_stays_inside_json():
    session = GatewaySession()
    text = session.mask("privateFunction", code=True) + "\u2028public"
    body = (
        "data: "
        + json.dumps(
            {"type": "response.output_text.delta", "delta": text}, ensure_ascii=False
        )
        + "\n\n"
    ).encode()
    assert (
        "privateFunction\u2028public"
        in session.response(body, "text/event-stream").decode()
    )
