"""Tests for code syntax and keyword preservation parser."""

from secure_mcp.engine.code_parser import CodeParser
from secure_mcp.models import TokenType


def test_keyword_preservation():
    code = "def process(user_id):\n    return user_id * 2"
    tokens = CodeParser.tokenize(code)

    keywords = [val for val, t_type, sub in tokens if sub == "keyword"]
    assert "def" in keywords
    assert "return" in keywords

    identifiers = [val for val, t_type, sub in tokens if t_type == TokenType.IDENTIFIER]
    assert "process" in identifiers
    assert "user_id" in identifiers


def test_sql_keywords():
    code = "SELECT user_name, email FROM accounts WHERE id = 101;"
    tokens = CodeParser.tokenize(code)

    keywords = [val for val, t_type, sub in tokens if sub == "keyword"]
    assert "SELECT" in keywords
    assert "FROM" in keywords
    assert "WHERE" in keywords


def test_string_literal_parsing():
    code = 'secret_key = "sk-live-supersecret123"'
    tokens = CodeParser.tokenize(code)

    literals = [val for val, t_type, sub in tokens if t_type == TokenType.LITERAL]
    assert len(literals) == 1
    assert "supersecret" in literals[0]
