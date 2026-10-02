"""Tests for surrogate token generation strategies."""

from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.models import SurrogateStrategy, TokenType


def test_bracket_strategy():
    gen = StrategyGenerator(SurrogateStrategy.BRACKET)
    s1 = gen.generate(TokenType.ENTITY, "Google")
    s2 = gen.generate(TokenType.NOUN, "search")
    assert s1 == "[ENT_1]"
    assert s2 == "[NOUN_1]"

    pattern = StrategyGenerator.get_pattern(SurrogateStrategy.BRACKET)
    assert pattern.match(s1)
    assert pattern.match(s2)


def test_unicode_strategy():
    gen = StrategyGenerator(SurrogateStrategy.UNICODE)
    s1 = gen.generate(TokenType.ENTITY, "Microsoft")
    s2 = gen.generate(TokenType.NUMBER, "5000")
    assert s1 == "⟦ENT_1⟧"
    assert s2 == "⟦NUM_1⟧"

    pattern = StrategyGenerator.get_pattern(SurrogateStrategy.UNICODE)
    assert pattern.match(s1)
    assert pattern.match(s2)


def test_pseudoword_strategy():
    gen = StrategyGenerator(SurrogateStrategy.PSEUDOWORD)
    s1 = gen.generate(TokenType.ENTITY, "Apple")
    s2 = gen.generate(TokenType.NOUN, "Computer")
    assert s1 != s2
    assert s1[1:-1].isalpha() and s1.startswith("⟪") and s1.endswith("⟫")
    assert s2[1:-1].isalpha() and s2.startswith("⟪") and s2.endswith("⟫")


def test_hash_strategy():
    gen = StrategyGenerator(SurrogateStrategy.HASH, salt="testsalt")
    s1 = gen.generate(TokenType.ENTITY, "Tesla")
    assert s1.startswith("~h1_") and s1.endswith("~")

    pattern = StrategyGenerator.get_pattern(SurrogateStrategy.HASH)
    assert pattern.match(s1)
