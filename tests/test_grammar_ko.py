"""Tests for Korean grammar, particle decomposition, and token classification."""

import pytest
from secure_mcp.engine.grammar_ko import (
    KoreanGrammarEngine,
    is_hangul_char,
    is_hangul_string,
    has_batchim,
)
from secure_mcp.models import TokenType


def test_hangul_detection():
    assert is_hangul_string("안녕하세요")
    assert is_hangul_string("AI플랫폼")
    assert not is_hangul_string("Hello World 123")


def test_batchim_detection():
    assert has_batchim("강")  # ㅇ 받침
    assert has_batchim("한")  # ㄴ 받침
    assert not has_batchim("가")
    assert not has_batchim("사")


def test_decompose_function_words():
    for word in ["그리고", "하지만", "따라서", "이것", "매우"]:
        stem, suffix, token_type = KoreanGrammarEngine.decompose_token(word)
        assert token_type == TokenType.GRAMMAR
        assert stem == word
        assert suffix == ""


def test_decompose_simple_josa():
    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("삼성전자가")
    assert stem == "삼성전자"
    assert suffix == "가"
    assert token_type == TokenType.ENTITY

    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("데이터를")
    assert stem == "데이터"
    assert suffix == "를"
    assert token_type == TokenType.NOUN


def test_decompose_compound_josa():
    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("서버로부터")
    assert stem == "서버"
    assert suffix == "로부터"

    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("카카오와")
    assert stem == "카카오"
    assert suffix == "와"


def test_decompose_plural_and_josa():
    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("고객들에게")
    assert stem == "고객"
    assert suffix == "들에게"

    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("환자들")
    assert stem == "환자"
    assert suffix == "들"


def test_decompose_copula():
    stem, suffix, token_type = KoreanGrammarEngine.decompose_token("병원입니다")
    assert stem == "병원"
    assert suffix == "입니다"
