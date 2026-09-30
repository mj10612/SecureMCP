"""Tests for English grammar and token classification."""

import pytest
from secure_mcp.engine.grammar_en import EnglishGrammarEngine, CLOSED_CLASS_WORDS
from secure_mcp.models import TokenType


def test_closed_class_words():
    for word in ["the", "in", "and", "is", "of", "to", "for", "with", "they", "will"]:
        assert EnglishGrammarEngine.is_grammatical(word)
        assert word in CLOSED_CLASS_WORDS


def test_classify_grammar_words():
    for word in ["the", "because", "although", "under", "should"]:
        token_type, should_mask = EnglishGrammarEngine.classify_token(word)
        assert token_type == TokenType.GRAMMAR
        assert not should_mask


def test_classify_entities():
    # Capitalized proper noun (not sentence start)
    token_type, should_mask = EnglishGrammarEngine.classify_token("Microsoft", is_sentence_start=False)
    assert token_type == TokenType.ENTITY
    assert should_mask

    # All-caps acronym
    token_type, should_mask = EnglishGrammarEngine.classify_token("NASA")
    assert token_type == TokenType.ENTITY
    assert should_mask

    # Email
    token_type, should_mask = EnglishGrammarEngine.classify_token("user@example.com")
    assert token_type == TokenType.ENTITY
    assert should_mask

    # Secret API key
    token_type, should_mask = EnglishGrammarEngine.classify_token("sk-proj-1234567890abcdef")
    assert token_type == TokenType.ENTITY
    assert should_mask


def test_classify_numbers():
    for num in ["$100,000", "42", "3.1415", "99.9%"]:
        token_type, should_mask = EnglishGrammarEngine.classify_token(num)
        assert token_type == TokenType.NUMBER
        assert should_mask


def test_classify_content_nouns():
    for noun in ["contract", "algorithm", "database", "hospital"]:
        token_type, should_mask = EnglishGrammarEngine.classify_token(noun)
        assert token_type == TokenType.NOUN
        assert should_mask
