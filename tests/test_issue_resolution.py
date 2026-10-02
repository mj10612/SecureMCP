"""Security, syntax, concurrency and bilingual regressions for issues #9-55."""

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Event
import time

from click.testing import CliRunner
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from secure_mcp import (
    LocalPrivacyClient,
    MaskingEngine,
    MaskMode,
    SessionVault,
    SurrogateStrategy,
)
from secure_mcp.cli import main
from secure_mcp.encrypted_session import (
    load_session,
    save_session,
    session_file_lock,
    _cipher,
    _MAGIC,
    _SALT_SIZE,
)
from secure_mcp.engine.grammar_ko import KoreanGrammarEngine
from secure_mcp.engine.strategies import mapping_key
from secure_mcp.engine.tokenizer import MultilingualTokenizer
from secure_mcp.models import TokenType
from secure_mcp.server import (
    app,
    mask_text,
    mask_code,
    unmask_text,
    vault,
    create_privacy_session,
    get_session_stats,
    get_privacy_status,
)
from secure_mcp.session import PrivacySession


@pytest.fixture(autouse=True)
def isolated_sessions():
    vault.clear_all()
    yield
    vault.clear_all()


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
@pytest.mark.parametrize(
    "secret",
    [
        "https://internal.example/api?token=secret123456",
        "s3://private-bucket/records",
        "`db_password=private`",
        '```python\nkey = "private"\n```',
        "abcdefab-cdef-abcd-efab-cdefabcdefab",
        "192.168.0.1",
        "2001:db8:85a3::8a2e:370:7334",
        "::1",
        "010-1234-5678",
        "+1-800-555-0199",
        "+82 10 1234 5678",
        "sk_live_51HxYzAbCdEfGhIjKl",
        "github_pat_11ABCDEFG0123456789_abcdefXYZ",
        "AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUvW",
        "glpat-abcdefghij1234567890",
        "xapp-abcdefghij1234567890",
        "ghs_abcdefghij1234567890",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "Müller",
        "Иван",
        "王小明",
        "홍길동",
        "김민서",
        "박하은",
        "O'Connor",
        "D'Angelo",
    ],
)
def test_sensitive_spans_mask_as_one_unit_and_restore(strategy, secret):
    result = json.loads(
        mask_text(secret, session_id="s", mode="entities_only", strategy=strategy.value)
    )
    assert result["masked_text"] != secret
    assert result["masked_tokens"] == 1
    restored = json.loads(unmask_text(result["masked_text"], "s", strict=True))
    assert restored["unmasked_text"] == secret
    assert restored["restored_unique_tokens"] == 1


@pytest.mark.parametrize(
    "text", ["50mg", "1a", "100억원", "150억원의", "10kg", "50mg/100ml"]
)
@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
def test_numeric_units_roundtrip(text, strategy):
    result = json.loads(mask_text(text, "s", strategy=strategy.value))
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == text


@pytest.mark.parametrize("mode", ["content_words", "entities_only", "aggressive"])
def test_punctuation_and_markdown_are_preserved(mode):
    text = "- well-known… 「회사」!\n- range 1-10。"
    result = json.loads(mask_text(text, "s", mode=mode))
    for punctuation in ["-", "…", "「", "」", "!", "\n", "。"]:
        assert result["masked_text"].count(punctuation) == text.count(punctuation)
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == text


def test_aggressive_masks_auxiliaries_and_function_adverbs():
    text = "The system is already very safe and we can trust it."
    content = json.loads(mask_text(text, "c"))
    aggressive = json.loads(mask_text(text, "a", mode="aggressive"))
    assert " already " in content["masked_text"]
    assert " already " not in aggressive["masked_text"]
    assert " is " not in aggressive["masked_text"]
    assert aggressive["masked_tokens"] > content["masked_tokens"]
    assert (
        json.loads(unmask_text(aggressive["masked_text"], "a"))["unmasked_text"] == text
    )


def test_pseudowords_never_rewrite_ordinary_names_and_report_only_delimited_candidates():
    result = json.loads(mask_text("Alice", "s", strategy="pseudoword"))
    response = result["masked_text"] + " Xerox Boron Praxis Smith Brivon"
    restored = json.loads(unmask_text(response, "s"))
    assert restored["unmasked_text"] == "Alice Xerox Boron Praxis Smith Brivon"
    assert restored["unmatched_surrogates"] == []
    engine, session = (
        MaskingEngine(),
        PrivacySession("p", strategy=SurrogateStrategy.PSEUDOWORD),
    )
    values = [
        engine._get_or_create_surrogate(
            str(i),
            kind,
            session.generator,
            session.forward_store,
            session.reverse_store,
        )
        for i in range(120)
        for kind in [TokenType.ENTITY, TokenType.NOUN]
    ]
    assert len(set(values)) == 240
    assert all(re.fullmatch(r"⟪[A-Z][a-z]+⟫", v) for v in values)


@pytest.mark.parametrize(
    "variant", ["[ENT-1]", "[ENT 1]", "[ent_1]", "[Entity_1]", "[ent1]"]
)
def test_altered_surrogates_are_reported_and_strict_mode_rejects(variant):
    mask_text("Alice", "s")
    assert json.loads(unmask_text(variant, "s"))["unmatched_surrogates"] == [variant]
    with pytest.raises(ToolError, match="altered"):
        unmask_text(variant, "s", strict=True)


def test_empty_reverse_store_still_reports_unknown_tokens():
    result = MaskingEngine().unmask("[ENT_999]", "empty", {})
    assert result.unmatched_surrogates == ["[ENT_999]"]


@pytest.mark.parametrize(
    "word",
    [
        "로그인",
        "확인",
        "승인",
        "원인",
        "개인",
        "결과",
        "사과",
        "내일",
        "회의",
        "국가",
        "인도",
        "대만",
        "통일",
        "김민서",
        "박하은",
        "새로운미지의이름",
    ],
)
def test_ambiguous_korean_bare_nouns_are_not_truncated(word):
    stem, suffix, _ = KoreanGrammarEngine.decompose_token(word)
    assert stem == word and suffix == ""


@pytest.mark.parametrize(
    "word,stem,suffix",
    [
        ("Apple은", "Apple", "은"),
        ("Google이", "Google", "이"),
        ("AWS로", "AWS", "로"),
        ("OpenAI와의", "OpenAI", "와의"),
        ("회의는", "회의", "는"),
        ("김민서에게", "김민서", "에게"),
    ],
)
def test_known_and_alphanumeric_korean_stems_keep_particles(word, stem, suffix):
    assert KoreanGrammarEngine.decompose_token(word)[:2] == (stem, suffix)
    result = json.loads(mask_text(word, "s"))
    assert result["masked_text"].endswith(suffix)
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == word


@pytest.mark.parametrize(
    "word,particle,expected",
    [
        ("사과", "이", "가"),
        ("사과", "을", "를"),
        ("회사", "과", "와"),
        ("고객", "는", "은"),
        ("서울", "으로", "로"),
        ("병원", "로", "으로"),
    ],
)
def test_generated_response_particle_normalization_is_explicit(
    word, particle, expected
):
    result = json.loads(mask_text(word, "s", sensitive_terms=[word]))
    response = result["masked_text"] + particle + " 좋다."
    normalized = json.loads(unmask_text(response, "s", normalize_particles=True))
    assert normalized["unmasked_text"] == word + expected + " 좋다."
    exact = json.loads(unmask_text(response, "s"))
    assert exact["unmasked_text"] == word + particle + " 좋다."


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
@pytest.mark.parametrize("mode", list(MaskMode))
@pytest.mark.parametrize(
    "language,text",
    [
        ("en", "Alice's account with Müller has 50mg and $1,250. The report is ready."),
        ("ko", "홍길동의 진료 기록과 삼성전자가 OpenAI와의 150억원 계약을 검토합니다."),
        ("auto", "Alice와 홍길동은 patient@example.com 및 010-1234-5678에 연락합니다."),
    ],
)
def test_bilingual_matrix(strategy, mode, language, text):
    if mode == MaskMode.CODE_AWARE:
        text = 'def 고객조회(patient_id):\n    return "private" + patient_id'
    result = json.loads(
        mask_text(
            text, "s", mode=mode.value, strategy=strategy.value, language=language
        )
    )
    assert (
        json.loads(unmask_text(result["masked_text"], "s", strict=True))[
            "unmasked_text"
        ]
        == text
    )
    if mode == MaskMode.CODE_AWARE:
        ast.parse(result["masked_text"])
    assert (
        result["total_tokens"] == result["masked_tokens"] + result["preserved_tokens"]
    )


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
@pytest.mark.parametrize(
    "language,code,hidden",
    [
        ("python", "def 고객조회(고객번호):\n    return 고객번호", "고객번호"),
        ("python", "private = key + string", "private"),
        (
            "python",
            "def f(set, list, index, type, key, error, log):\n    return set + index",
            "index",
        ),
        ("python", 'value = "first\\\nprivate\\\nlast"', "private"),
        ("python", 'value = f"{customer["private"]}"', "private"),
        ("javascript", "const token = `private`;", "private"),
        ("javascript", "const select = key;", "select"),
        ("javascript", "const x = `private ${`inside ${secret}`}`;", "secret"),
        ("go", "var token = `private\nraw secret`", "private"),
        ("cpp", 'auto token = R"tag(first"private"last)tag";', "private"),
        ("rust", 'let token = r#"first\nprivate\nlast"#;', "private"),
        ("sql", "SELECT print FROM console; -- private", "private"),
        ("sql", "SELECT 'patient''s private data' FROM accounts;", "private"),
        ("java", "long price = 9876543210L;", "9876543210"),
        ("python", "price = 1_250_000\nmask = 0b1011\nval = 0o17", "1_250_000"),
        ("javascript", "const big = 12345678901234567890n;", "1234567890"),
    ],
)
def test_language_literals_and_identifiers_do_not_leak(
    strategy, language, code, hidden
):
    result = json.loads(mask_code(code, "s", strategy.value, language))
    assert hidden not in result["masked_text"]
    assert (
        json.loads(unmask_text(result["masked_text"], "s", strict=True))[
            "unmasked_text"
        ]
        == code
    )
    if language == "python" and 'f"{customer[' not in code:
        ast.parse(result["masked_text"])


@pytest.mark.parametrize(
    "language,code,keywords",
    [
        (
            "rust",
            "use std::io; mod secret; unsafe fn read() {}",
            ["use", "mod", "unsafe", "fn"],
        ),
        (
            "java",
            "public synchronized void update() throws IOException {}",
            ["public", "synchronized", "void", "throws"],
        ),
        (
            "go",
            "func main() { goto label; switch x { case 1: fallthrough } }",
            ["func", "goto", "fallthrough"],
        ),
    ],
)
def test_core_keywords_preserved_per_language(language, code, keywords):
    result = json.loads(mask_code(code, "s", language=language))
    assert all(re.search(r"\b" + k + r"\b", result["masked_text"]) for k in keywords)


def test_repository_python_code_is_parseable_after_masking():
    code = Path("src/secure_mcp/session.py").read_text(encoding="utf-8")
    result = json.loads(mask_code(code, "s", language="python"))
    compile(result["masked_text"], "<masked>", "exec")
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == code


def test_same_original_gets_distinct_type_and_context_allocations():
    engine, session = MaskingEngine(), PrivacySession("s")
    engine.mask_text(
        "user", "s", session.generator, session.forward_store, session.reverse_store
    )
    code = engine.mask_code(
        'user = "user"',
        "s",
        session.generator,
        session.forward_store,
        session.reverse_store,
    )
    assert "[NOUN_1]" not in code.masked_text
    mappings = [m for m in session.forward_store.values() if m.original == "user"]
    assert {m.token_type for m in mappings} == {
        TokenType.NOUN,
        TokenType.IDENTIFIER,
        TokenType.LITERAL,
    }
    assert len({m.surrogate for m in mappings}) == 3


def test_hash_nonces_are_independent_of_original_and_public_salt():
    sessions = [
        PrivacySession("public", strategy=SurrogateStrategy.HASH, salt="public")
        for _ in range(2)
    ]
    engine = MaskingEngine()
    tokens = [
        engine.mask_text(
            "85000", s.session_id, s.generator, s.forward_store, s.reverse_store
        ).masked_text
        for s in sessions
    ]
    assert tokens[0] != tokens[1]
    assert all(re.fullmatch(r"~h1_[a-f0-9]{24}~", t) for t in tokens)


def test_metrics_distinguish_occurrences_and_unique_allocations():
    masked = json.loads(mask_text("Alice Alice and Alice", "s"))
    restored = json.loads(unmask_text(masked["masked_text"], "s"))
    assert restored["restored_occurrences"] == 3
    assert restored["restored_unique_tokens"] == 1
    assert masked["masked_ratio"] == 0.75


@pytest.mark.parametrize(
    "kwargs", [{"mode": "bogus"}, {"strategy": "bogus"}, {"language": "bogus"}]
)
def test_invalid_policy_fails_before_allocating_session(kwargs):
    with pytest.raises(ToolError):
        mask_text("Alice", "s", **kwargs)
    assert vault.active_count == 0


@pytest.mark.parametrize("ttl", [0, -1, 86401])
def test_invalid_ttl_is_rejected(ttl):
    with pytest.raises(ToolError, match="ttl_seconds"):
        create_privacy_session("s", ttl_seconds=ttl)
    assert vault.active_count == 0


def test_duplicate_creation_does_not_mutate_live_session():
    create_privacy_session("s", ttl_seconds=60)
    mask_text("Alice", "s")
    with pytest.raises(ToolError, match="already exists"):
        create_privacy_session("s", ttl_seconds=100)
    session = vault.get_session("s")
    assert session.ttl_seconds == 60
    assert len(session.forward_store) == 1
    mask_code("x = 1", "s")
    assert json.loads(get_session_stats("s"))["mode"] == "code_aware"
    assert json.loads(get_privacy_status())["active_sessions"] == 1


def test_idle_expiration_releases_mapping_references_without_a_read():
    local = SessionVault(default_ttl=0.03, cleanup_interval=0.005)
    session = local.get_or_create("s")
    MaskingEngine().mask_text(
        "Secret", "s", session.generator, session.forward_store, session.reverse_store
    )
    deadline = time.monotonic() + 2
    while session.forward_store and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session.forward_store and not session.reverse_store
    with pytest.raises(ValueError, match="cleared"):
        MaskingEngine().mask_text(
            "Secret",
            "s",
            session.generator,
            session.forward_store,
            session.reverse_store,
        )
    local.close()


def test_concurrent_masks_preserve_bijection_and_counts():
    texts = [f"Client{i} met Alice." for i in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: json.loads(mask_text(t, "s")), texts))
    session = vault.get_session("s")
    assert len(session.forward_store) == len(session.reverse_store)
    assert session.total_mask_calls == 40
    for text, result in zip(texts, results):
        assert (
            json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == text
        )
    assert (
        session.forward_store[mapping_key("Alice", TokenType.ENTITY)].occurrence_count
        == 40
    )


def test_clear_waits_for_inflight_mask_and_prevents_post_purge_writes():
    local = SessionVault()
    session = local.get_or_create("s")
    entered, resume, clearing = Event(), Event(), Event()
    generate = session.generator.generate

    def paused(*args, **kwargs):
        entered.set()
        assert resume.wait(3)
        return generate(*args, **kwargs)

    session.generator.generate = paused

    def clear():
        clearing.set()
        return local.clear_session("s")

    with ThreadPoolExecutor(max_workers=2) as pool:
        masking = pool.submit(
            MaskingEngine().mask_text,
            "Secret",
            "s",
            session.generator,
            session.forward_store,
            session.reverse_store,
        )
        assert entered.wait(3)
        purging = pool.submit(clear)
        assert clearing.wait(3)
        assert not purging.done()
        resume.set()
        masking.result(timeout=3)
        assert purging.result(timeout=3)
    assert not session.forward_store and not session.reverse_store
    with pytest.raises(ValueError, match="cleared"):
        session.generator.generate(TokenType.NOUN, "NewSecret")
    local.close()


def test_schema_version_and_generator_change_do_not_destroy_snapshots(
    tmp_path, monkeypatch
):
    session = PrivacySession("s", strategy=SurrogateStrategy.PSEUDOWORD)
    result = MaskingEngine().mask_text(
        "Alice", "s", session.generator, session.forward_store, session.reverse_store
    )
    path = tmp_path / "session.enc"
    save_session(path, session, "pw")
    monkeypatch.setattr(
        type(session.generator), "generate", lambda *a, **k: "different-algorithm"
    )
    restored = load_session(path, "pw", "s")
    assert (
        MaskingEngine()
        .unmask(result.masked_text, "s", restored.reverse_store, session.strategy)
        .unmasked_text
        == "Alice"
    )
    data = path.read_bytes()
    salt = data[len(_MAGIC) : len(_MAGIC) + _SALT_SIZE]
    cipher = _cipher("pw", salt)
    payload = json.loads(cipher.decrypt(data[len(_MAGIC) + _SALT_SIZE :]))
    payload["version"] = 999
    path.write_bytes(_MAGIC + salt + cipher.encrypt(json.dumps(payload).encode()))
    with pytest.raises(ValueError, match="Unsupported.*version"):
        load_session(path, "pw", "s")


def test_cli_file_lock_covers_read_modify_write(tmp_path):
    path = tmp_path / "s.enc"
    with session_file_lock(path):
        result = CliRunner().invoke(
            main,
            ["mask", "Alice", "--session-file", str(path)],
            env={"SECURE_MCP_SESSION_PASSWORD": "pw"},
        )
        assert result.exit_code != 0 and "in use" in result.output
    assert not path.exists() and not path.with_name("s.enc.lock").exists()


@pytest.mark.asyncio
async def test_model_cannot_call_restoration_oracle():
    for name in ["unmask_text", "unmask_code"]:
        with pytest.raises(ToolError, match="Unknown tool"):
            await app.call_tool(name, {"masked_text": "[ENT_1]"})
    assert vault.default_session_id != SessionVault().default_session_id
    assert (
        CliRunner().invoke(main, ["serve", "--transport", "streamable-http"]).exit_code
        != 0
    )


def test_trusted_client_only_sends_masked_data_and_clears_on_provider_failure():
    with LocalPrivacyClient() as client:
        received = []

        def provider(payload):
            received.append(payload)
            return payload

        restored = client.request(
            "Patient John Doe received 50mg.", provider, sensitive_terms={"John Doe"}
        )
        assert "John" not in received[0] and "50mg" not in received[0]
        assert restored.unmasked_text == "Patient John Doe received 50mg."
        assert client.vault.active_count == 0

        def failing(payload):
            raise RuntimeError("provider failed")

        with pytest.raises(RuntimeError):
            client.request("Secret", failing)
        assert client.vault.active_count == 0


def test_explicit_terms_and_preserve_are_available_in_cli_and_mcp():
    result = json.loads(
        mask_text(
            "john doe deployed Kubernetes",
            "s",
            mode="entities_only",
            sensitive_terms=["john doe"],
            custom_preserve=["Kubernetes"],
        )
    )
    assert (
        "john doe" not in result["masked_text"]
        and "Kubernetes" in result["masked_text"]
    )
    cli = CliRunner().invoke(
        main,
        [
            "mask",
            "john doe",
            "--sensitive-term",
            "john doe",
            "--mode",
            "entities_only",
            "--json-output",
        ],
    )
    assert (
        cli.exit_code == 0 and "john doe" not in json.loads(cli.output)["masked_text"]
    )


def test_examples_restore_correct_facts():
    cases = [
        (
            "examples/openai_agent_privacy.py",
            ["John Doe", "St. Jude", "50mg", "leukemia"],
        ),
        ("examples/claude_agent_privacy.py", ["150억원", "네이버", "카카오페이"]),
    ]
    for path, required in cases:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        restored = result.stdout.split("RESTORED:")[-1]
        assert all(value in restored for value in required)


def test_unicode_tokenizer_keeps_words_atomic():
    for text in ["Müller", "Иван", "王小明", "고객번호"]:
        chunks = MultilingualTokenizer.tokenize(text)
        assert len(chunks) == 1 and chunks[0].is_word


def test_python_floor_division_and_contextual_keywords_preserve_syntax():
    code = "match = key\ncase = match\nvalue = 10 // 2\nmatch value:\n    case 1: pass"
    result = json.loads(mask_code(code, "s", language="python"))
    assert " // " in result["masked_text"]
    assert not result["masked_text"].startswith("match =")
    assert (
        "\nmatch " in result["masked_text"] and "\n    case " in result["masked_text"]
    )
    ast.parse(result["masked_text"])
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == code


@pytest.mark.parametrize(
    "language,code",
    [
        ("go", "var token = `private { unmatched`"),
        ("rust", "fn get<'a>(value: &'a str) -> &'a str { value }"),
    ],
)
def test_raw_braces_and_rust_lifetimes_are_supported(language, code):
    result = json.loads(mask_code(code, "s", language=language))
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == code
    assert "private" not in result["masked_text"]


@pytest.mark.parametrize("code", ['x = "private', 'x = R"tag(private', "/* private"])
def test_unterminated_literals_fail_closed(code):
    with pytest.raises(ToolError, match="Unterminated"):
        mask_code(code, "s", language="cpp")


def test_legacy_pseudoword_snapshot_can_be_read_updated_and_read_again(tmp_path):
    import os
    from secure_mcp.models import TokenMapping

    path = tmp_path / "legacy.enc"
    session = PrivacySession("s", strategy=SurrogateStrategy.PSEUDOWORD)
    payload = {
        "session_id": "s",
        "mode": "content_words",
        "strategy": "pseudoword",
        "ttl_seconds": 3600,
        "created_at": session.created_at,
        "last_accessed": session.last_accessed,
        "total_mask_calls": 1,
        "total_unmask_calls": 0,
        "salt": "s",
        "mappings": [
            {"original": "Alice", "surrogate": "Brivon", "token_type": "entity"}
        ],
    }
    salt = os.urandom(_SALT_SIZE)
    path.write_bytes(
        _MAGIC + salt + _cipher("pw", salt).encrypt(json.dumps(payload).encode())
    )
    loaded = load_session(path, "pw", "s")
    assert loaded.reverse_store["Brivon"].format_version == 1
    MaskingEngine().mask_text(
        "Bob", "s", loaded.generator, loaded.forward_store, loaded.reverse_store
    )
    save_session(path, loaded, "pw")
    reloaded = load_session(path, "pw", "s")
    assert (
        MaskingEngine()
        .unmask("Brivon", "s", reloaded.reverse_store, SurrogateStrategy.PSEUDOWORD)
        .unmasked_text
        == "Alice"
    )
    assert len(reloaded.forward_store) == 2
    assert (
        TokenMapping.model_validate(reloaded.reverse_store["Brivon"]).format_version
        == 1
    )


def test_cli_demo_uses_actual_allocations():
    result = CliRunner().invoke(main, ["demo"])
    assert result.exit_code == 0, result.output
    assert "150억원" in result.output and "50mg" in result.output


@pytest.mark.parametrize("strategy", list(SurrogateStrategy))
def test_all_code_strategies_keep_python_bytes_and_literals_parseable(strategy):
    code = "value = b'private'\nratio = 1.5\nraw = rf'private {ratio}'"
    result = json.loads(mask_code(code, "s", strategy.value, "python"))
    ast.parse(result["masked_text"])
    assert "private" not in result["masked_text"]
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == code


def test_masked_python_numeric_patterns_compile():
    code = "match status:\n    case 1: pass\n    case 2: pass\n    case _: pass"
    result = json.loads(mask_code(code, "s", language="python"))
    compile(result["masked_text"], "<masked>", "exec")
    assert json.loads(unmask_text(result["masked_text"], "s"))["unmasked_text"] == code


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_javascript_masked_source_passes_native_syntax_check(tmp_path):
    code = "const secret = `private ${name}`; function process(index) { return index + 123n; }"
    result = json.loads(mask_code(code, "s", language="javascript"))
    path = tmp_path / "masked.js"
    path.write_text(result["masked_text"], encoding="utf-8")
    checked = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, timeout=10
    )
    assert checked.returncode == 0, checked.stderr
    assert (
        "private" not in result["masked_text"] and "index" not in result["masked_text"]
    )
