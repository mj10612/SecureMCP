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
    unmask_res = engine.unmask(simulated_ai, "test_ko", reverse, strategy=SurrogateStrategy.UNICODE)
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
