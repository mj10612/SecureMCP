"""Tests for core masking engine (masking, unmasking, round-trip restoration)."""

import pytest

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.models import MaskMode, SurrogateStrategy


def test_roundtrip_english_text():
    engine = MaskingEngine()
    gen = StrategyGenerator(SurrogateStrategy.BRACKET)
    forward = {}
    reverse = {}

    text = "Yesterday, Pfizer announced a $43,000,000 acquisition of Seagen to accelerate oncology drug development."
    mask_res = engine.mask_text(
        text=text,
        session_id="test_sess",
        generator=gen,
        mapping_store=forward,
        reverse_store=reverse,
        mode=MaskMode.CONTENT_WORDS,
        strategy=SurrogateStrategy.BRACKET,
    )

    assert mask_res.masked_tokens > 0
    assert "Pfizer" not in mask_res.masked_text
    assert "Seagen" not in mask_res.masked_text
    assert "$43,000,000" not in mask_res.masked_text
    # Prepositions and grammar words preserved
    assert " of " in mask_res.masked_text
    assert " to " in mask_res.masked_text
    assert " a " in mask_res.masked_text

    # Simulated AI response using the masked tokens
    simulated_ai = mask_res.masked_text
    unmask_res = engine.unmask(simulated_ai, "test_sess", reverse)

    assert unmask_res.unmasked_text == text
    assert unmask_res.restored_tokens_count == mask_res.masked_tokens


def test_roundtrip_korean_text():
    engine = MaskingEngine()
    gen = StrategyGenerator(SurrogateStrategy.UNICODE)
    forward = {}
    reverse = {}

    text = "삼성전자가 카카오와 협력하여 보안 솔루션을 구축했습니다."
    mask_res = engine.mask_text(
        text=text,
        session_id="test_ko",
        generator=gen,
        mapping_store=forward,
        reverse_store=reverse,
        strategy=SurrogateStrategy.UNICODE,
        language="ko",
    )

    # Korean particles should be preserved attached to surrogate
    assert "가 " in mask_res.masked_text or "가" in mask_res.masked_text
    assert "와 " in mask_res.masked_text or "와" in mask_res.masked_text

    simulated_ai = mask_res.masked_text
    unmask_res = engine.unmask(
        simulated_ai, "test_ko", reverse, strategy=SurrogateStrategy.UNICODE
    )
    assert unmask_res.unmasked_text == text


def test_roundtrip_code():
    engine = MaskingEngine()
    gen = StrategyGenerator(SurrogateStrategy.BRACKET)
    forward = {}
    reverse = {}

    code = "def calc_revenue(client_id, tax_rate):\n    return client_id * tax_rate"
    mask_res = engine.mask_code(
        code=code,
        session_id="code_sess",
        generator=gen,
        mapping_store=forward,
        reverse_store=reverse,
    )

    assert "def " in mask_res.masked_text
    assert "return " in mask_res.masked_text
    assert "calc_revenue" not in mask_res.masked_text
    assert "client_id" not in mask_res.masked_text

    unmask_res = engine.unmask(mask_res.masked_text, "code_sess", reverse)
    assert unmask_res.unmasked_text == code


@pytest.mark.parametrize(
    "code",
    [
        "version = 0.5.1",
        "release = 1.2.3",
        "ip = 192.168.0.1",
        "x = 10.20.30.40",
        'uri = "https://example.com:8443/v1.2.3"',
    ],
)
def test_dotted_numbers_roundtrip_as_one_token(code):
    engine = MaskingEngine()
    gen = StrategyGenerator(SurrogateStrategy.UNICODE)
    forward = {}
    reverse = {}
    mask_res = engine.mask_code(code, "dotted", gen, forward, reverse)
    unmask_res = engine.unmask(mask_res.masked_text, "dotted", reverse, strict=True)
    assert unmask_res.unmasked_text == code
    assert unmask_res.unmatched_surrogates == []


def test_legacy_glued_numeric_aliases_are_restored_and_reported():
    engine = MaskingEngine()
    gen = StrategyGenerator(SurrogateStrategy.UNICODE)
    forward = {}
    reverse = {}

    engine.mask_code("first = 1.2\nsecond = 3.4", "legacy", gen, forward, reverse)
    aliases = {
        key.split(":", 2)[2]: mapping.surrogate
        for key, mapping in forward.items()
        if key.startswith("code:number:")
    }
    glued = aliases["1.2"] + aliases["3.4"]
    # A foreign digit run that only shares the prefix is not a valid alias.
    foreign = "732846" + "9" * 12 + aliases["1.2"]

    result = engine.unmask(f"v = {glued}\nw = {foreign}", "legacy", reverse)
    assert result.unmasked_text == "v = 1.23.4\nw = 7328469999999999991.2"
    assert sorted(result.unmatched_surrogates) == ["732846999999999999"]
    with pytest.raises(ValueError, match="Unrecognized"):
        engine.unmask(foreign, "legacy", reverse, strict=True)
