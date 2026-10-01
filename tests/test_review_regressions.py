"""End-to-end regression coverage for review issues #1 through #8."""

import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from secure_mcp.cli import main
from secure_mcp.encrypted_session import load_session, save_session
from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.models import MaskMode, SurrogateStrategy
from secure_mcp.server import app, mask_text, mask_code, unmask_text, unmask_code, vault
from secure_mcp.session import PrivacySession, SessionVault


@pytest.fixture(autouse=True)
def isolated_review_sessions():
    vault.clear_all()
    yield
    vault.clear_all()


@pytest.mark.parametrize("code", [
    'token = f"sk-live-supersecret123"',
    "token = f'sk-live-supersecret123'",
    'token = f"""sk-live-supersecret123\n{private_name}"""',
    "token = F'''sk-live-supersecret123\n{private_name}'''",
    'token = rf"sk-live-supersecret123"',
    "token = Fr'sk-live-supersecret123'",
    'token = b"sk-live-supersecret123"',
    'token = r"sk-live-supersecret123"',
    '# api_key=sk-live-supersecret123\ntoken = 1',
    '// api_key=sk-live-supersecret123\ntoken = 1;',
    '/* api_key=sk-live-supersecret123\nprivate_name */\ntoken = 1;',
])
def test_code_literals_and_comments_do_not_leak(code):
    masked = json.loads(mask_code(code, session_id="review"))
    assert "sk-live-supersecret123" not in masked["masked_text"]
    assert "private_name" not in masked["masked_text"]
    restored = json.loads(unmask_code(masked["masked_text"], session_id="review"))
    assert restored["unmasked_code"] == code
    assert restored["restored_tokens_count"] == masked["masked_tokens"]


def test_empty_literals_are_preserved_not_counted_as_masked():
    code = 'value = ""\n#\n/**/'
    masked = json.loads(mask_code(code, session_id="review"))
    assert masked["masked_tokens"] == 1
    assert masked["total_tokens"] == masked["masked_tokens"] + masked["preserved_tokens"]
    assert json.loads(unmask_code(masked["masked_text"], session_id="review"))["unmasked_code"] == code


@pytest.mark.parametrize("prefix", ["sk-", "ak-", "ghp_", "gho_", "xoxb-", "xoxp-", "sec_"])
def test_entities_only_masks_complete_secret(prefix):
    secret = prefix + "proj-supersecretabcdef123456"
    text = f"the key is {secret}."
    masked = json.loads(mask_text(text, session_id="review", mode="entities_only"))
    assert secret not in masked["masked_text"]
    assert "supersecret" not in masked["masked_text"]
    assert masked["masked_tokens"] == 1
    assert json.loads(unmask_text(masked["masked_text"], session_id="review"))["unmasked_text"] == text


@pytest.mark.parametrize("text", ["Alice met Bob.", "  Alice met Bob.", "Alice's account.", "Hello. Alice met Bob."])
def test_entities_only_masks_initial_names(text):
    masked = json.loads(mask_text(text, session_id="review", mode="entities_only"))
    assert "Alice" not in masked["masked_text"]
    assert "Bob" not in masked["masked_text"]
    assert json.loads(unmask_text(masked["masked_text"], session_id="review"))["unmasked_text"] == text


def test_entities_only_still_preserves_initial_function_words():
    text = "The account is with Alice."
    masked = json.loads(mask_text(text, session_id="review", mode="entities_only"))
    assert masked["masked_text"] == "The account is with [ENT_1]."


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
def test_literal_whitespace_and_distinct_values_roundtrip(strategy):
    code = 'values = ["x", " x", "x ", " x ", "   ", """\n x \n"""]'
    masked = json.loads(mask_code(code, session_id="review", strategy=strategy.value))
    restored = json.loads(unmask_code(masked["masked_text"], session_id="review"))
    assert restored["unmasked_code"] == code
    assert len({vault.get_session("review").forward_store[s].surrogate
                for s in ["x", " x", "x ", " x ", "   ", "\n x \n"]}) == 6


def test_pseudoword_restoration_does_not_cascade():
    masked = json.loads(mask_text("Alpha Brivon", session_id="review", strategy="pseudoword"))
    restored = json.loads(unmask_text(masked["masked_text"], session_id="review"))
    assert restored["unmasked_text"] == "Alpha Brivon"
    assert restored["restored_tokens_count"] == 2
    assert restored["unmatched_surrogates"] == []


def test_pseudoword_restoration_respects_word_boundaries():
    mask_text("Alpha", session_id="review", strategy="pseudoword")
    response = "Brivonian preBrivon Brivon_suffix Brivon, Brivon."
    restored = json.loads(unmask_text(response, session_id="review"))
    assert restored["unmasked_text"] == "Brivonian preBrivon Brivon_suffix Alpha, Alpha."
    assert restored["restored_tokens_count"] == 2


def test_pseudowords_with_korean_particles_roundtrip():
    text = "삼성전자가 카카오와 협력합니다."
    masked = json.loads(mask_text(text, session_id="review", strategy="pseudoword"))
    assert json.loads(unmask_text(masked["masked_text"], session_id="review"))["unmasked_text"] == text


def test_unmatched_tokens_are_scanned_before_restoration():
    code = 'value = "[ID_999]"'
    masked = json.loads(mask_code(code, session_id="review"))
    restored = json.loads(unmask_code(masked["masked_text"] + " [ID_888]", session_id="review"))
    assert restored["unmasked_code"] == code + " [ID_888]"
    assert restored["unmatched_surrogates"] == ["[ID_888]"]


def test_code_aware_dispatch_preserves_keywords():
    code = 'def process(user):\n    return "private-value" + user'
    masked = json.loads(mask_text(code, session_id="review", mode="code_aware"))
    assert masked["mode"] == "code_aware"
    assert masked["detected_language"] == "code"
    assert "def " in masked["masked_text"] and "return " in masked["masked_text"]
    assert "private-value" not in masked["masked_text"]
    assert json.loads(unmask_text(masked["masked_text"], session_id="review"))["unmasked_text"] == code


def test_cli_code_aware_dispatch():
    result = CliRunner().invoke(main, ["mask", "def process(user):\n    return user", "--mode", "code_aware"])
    assert result.exit_code == 0
    assert "def [ID_1]([ID_2])" in result.output
    assert "return [ID_2]" in result.output


@pytest.mark.parametrize("first", list(SurrogateStrategy))
def test_vault_rejects_strategy_changes_without_mutation(first):
    local_vault = SessionVault()
    session = local_vault.get_or_create("fixed", strategy=first)
    other = next(s for s in SurrogateStrategy if s != first)
    last_accessed = session.last_accessed
    with pytest.raises(ValueError, match="cannot switch"):
        local_vault.get_or_create("fixed", strategy=other)
    assert session.strategy == session.generator.strategy == first
    assert session.last_accessed == last_accessed


@pytest.mark.asyncio
async def test_mcp_reports_strategy_mismatch_as_tool_error():
    await app.call_tool("mask_text", {"text": "first", "session_id": "review"})
    for name, field in [("mask_text", "text"), ("mask_code", "code")]:
        with pytest.raises(ToolError, match="cannot switch") as error:
            await app.call_tool(name, {field: "second", "session_id": "review", "strategy": "hash"})
        assert not isinstance(error.value, UnexpectedToolError)
    assert vault.get_session("review").total_mask_calls == 1


def test_unmask_infers_strategy_and_rejects_explicit_mismatch():
    masked = json.loads(mask_text("Alice", session_id="review", strategy="hash"))
    restored = json.loads(unmask_text(masked["masked_text"] + " ~h999_abcd~", session_id="review"))
    assert restored["unmasked_text"] == "Alice ~h999_abcd~"
    assert restored["unmatched_surrogates"] == ["~h999_abcd~"]
    with pytest.raises(ToolError, match="must match"):
        unmask_text(masked["masked_text"], session_id="review", strategy="bracket")


def test_core_metadata_uses_actual_generator_strategy():
    session = PrivacySession("review", strategy=SurrogateStrategy.UNICODE)
    result = MaskingEngine().mask_text("Alice", session.session_id, session.generator,
                                       session.forward_store, session.reverse_store)
    assert result.strategy == SurrogateStrategy.UNICODE
    assert result.masked_text.startswith("⟦")


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
def test_cli_roundtrip_across_real_processes(tmp_path, strategy):
    path = tmp_path / "session.enc"
    env = dict(os.environ, SECURE_MCP_SESSION_PASSWORD="synthetic-test-password")
    common = ["--session-id", "review", "--session-file", str(path)]
    text = "Alice from Google"
    expected = PrivacySession("review", strategy=strategy)
    masked = MaskingEngine().mask_text(text, "review", expected.generator,
                                      expected.forward_store, expected.reverse_store).masked_text
    def run(args):
        return subprocess.run([sys.executable, "-m", "secure_mcp", *args],
                              env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    result = run(["mask", text, "--strategy", strategy.value, *common])
    assert result.returncode == 0, result.stderr
    assert masked in result.stdout
    assert b"Alice" not in path.read_bytes()
    assert b"Google" not in path.read_bytes()
    result = run(["unmask", masked, *common])
    assert result.returncode == 0, result.stderr
    assert text in result.stdout
    # Another process appends mappings without reusing existing surrogates.
    result = run(["mask", "Bob", "--strategy", strategy.value, *common])
    assert result.returncode == 0, result.stderr
    loaded = load_session(path, env["SECURE_MCP_SESSION_PASSWORD"], "review")
    assert set(loaded.forward_store) == {"Alice", "Google", "Bob"}
    assert len(loaded.reverse_store) == 3
    result = run(["unmask", masked, *common])
    assert result.returncode == 0 and text in result.stdout


def test_encrypted_session_rejects_wrong_password_tampering_and_wrong_id(tmp_path):
    path = tmp_path / "session.enc"
    save_session(path, PrivacySession("review"), "password")
    with pytest.raises(ValueError, match="password or damaged"):
        load_session(path, "wrong", "review")
    with pytest.raises(ValueError, match="different session ID"):
        load_session(path, "password", "other")
    data = bytearray(path.read_bytes())
    data[-10] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="password or damaged"):
        load_session(path, "password", "review")


def test_encrypted_session_rejects_expiration(tmp_path):
    path = tmp_path / "session.enc"
    session = PrivacySession("review", ttl_seconds=1)
    session.last_accessed -= 10
    save_session(path, session, "password")
    with pytest.raises(ValueError, match="expired"):
        load_session(path, "password", "review")


def test_cli_strategy_mismatch_leaves_encrypted_file_unchanged(tmp_path):
    path = tmp_path / "session.enc"
    runner = CliRunner()
    options = ["--session-id", "review", "--session-file", str(path)]
    env = {"SECURE_MCP_SESSION_PASSWORD": "test-password"}
    assert runner.invoke(main, ["mask", "Alice", *options], env=env).exit_code == 0
    encrypted = path.read_bytes()
    for command, text in [("mask", "Bob"), ("unmask", "[NOUN_1]")]:
        result = runner.invoke(main, [command, text, "--strategy", "hash", *options], env=env)
        assert result.exit_code != 0
        assert "uses strategy" in result.output
        assert path.read_bytes() == encrypted


def test_cli_explains_missing_process_session():
    result = CliRunner().invoke(main, ["unmask", "[NOUN_1]", "--session-id", "missing"])
    assert result.exit_code != 0
    assert "--session-file" in result.output
