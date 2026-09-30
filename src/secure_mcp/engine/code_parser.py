"""Code syntax parser and language keyword preservation engine for SecureMCP."""

from __future__ import annotations

import re
from typing import List, Set, Tuple
from secure_mcp.models import TokenType


# Common keywords across major programming languages (Python, JS/TS, Go, Rust, Java, C/C++, SQL, Shell)
CODE_KEYWORDS: Set[str] = {
    # Python
    "def", "class", "return", "if", "elif", "else", "while", "for", "in", "try",
    "except", "finally", "with", "as", "import", "from", "async", "await", "lambda",
    "yield", "pass", "break", "continue", "raise", "global", "nonlocal", "assert",
    "True", "False", "None", "and", "or", "not", "is", "self", "cls",

    # JavaScript / TypeScript
    "function", "const", "let", "var", "switch", "case", "default", "do",
    "throw", "new", "extends", "super", "this", "export", "typeof",
    "instanceof", "void", "delete", "of", "interface", "type", "enum", "implements",
    "public", "private", "protected", "readonly", "abstract", "static", "null",
    "undefined", "true", "false", "any", "unknown", "never", "string", "number", "boolean",

    # Rust / Go / C / Java
    "fn", "struct", "impl", "trait", "pub", "mut", "match", "loop", "package", "func",
    "go", "select", "chan", "defer", "int", "float", "double", "char", "void", "short",
    "long", "signed", "unsigned", "sizeof", "typedef", "volatile", "register", "auto",
    "extern", "inline", "virtual", "override", "final", "namespace", "using", "template",
    "typename", "operator", "friend", "constexpr", "nullptr",

    # SQL
    "SELECT", "select", "FROM", "from", "WHERE", "where", "JOIN", "join",
    "LEFT", "left", "RIGHT", "right", "INNER", "inner", "OUTER", "outer",
    "ON", "on", "INSERT", "insert", "INTO", "into", "VALUES", "values",
    "UPDATE", "update", "SET", "set", "DELETE", "delete", "GROUP", "group",
    "BY", "by", "ORDER", "order", "HAVING", "having", "LIMIT", "limit",
    "OFFSET", "offset", "CREATE", "create", "TABLE", "table", "DROP", "drop",
    "ALTER", "alter", "INDEX", "index", "AS", "as", "UNION", "union",
    "AND", "and", "OR", "or", "NOT", "not", "NULL", "null", "IS", "is",
    "PRIMARY", "primary", "KEY", "key", "FOREIGN", "foreign", "REFERENCES", "references",

    # Common built-in runtime symbols to keep readable
    "print", "len", "range", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "super", "isinstance", "issubclass", "enumerate", "zip", "map", "filter",
    "any", "all", "min", "max", "sum", "abs", "round", "open", "console", "log",
    "error", "warn", "Math", "JSON", "Promise", "Array", "Object", "String", "Number",
    "Boolean", "setTimeout", "setInterval", "fetch", "fmt", "Println", "Printf",
    "std", "cout", "cin", "endl", "vector", "string",
}

# Regex for code tokenization
TOKEN_PATTERN = re.compile(
    r'(?P<WHITESPACE>\s+)'
    r'|(?P<COMMENT>#.*$|//.*$|/\*[\s\S]*?\*/)'
    r'|(?P<STRING>f?"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|f?"[^"\\]*(?:\\.[^"\\]*)*"|f?\'[^\'\\]*(?:\\.[^\'\\]*)*\')'
    r'|(?P<NUMBER>\b0x[0-9a-fA-F]+\b|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)'
    r'|(?P<IDENTIFIER>\b[a-zA-Z_][a-zA-Z0-9_]*\b)'
    r'|(?P<PUNCTUATION>[\(\)\[\]\{\}\.,:;])'
    r'|(?P<OPERATOR>[+\-*/%=!&|^~<>@?]+)'
    r'|(?P<OTHER>.)',
    re.MULTILINE
)


class CodeParser:
    """Parses code snippets, classifying tokens into syntactic structures vs identifiers/literals."""

    @staticmethod
    def tokenize(code: str) -> List[Tuple[str, TokenType, str]]:
        """Tokenize code into (raw_text, token_type, subcategory)."""
        tokens: List[Tuple[str, TokenType, str]] = []

        for match in TOKEN_PATTERN.finditer(code):
            kind = match.lastgroup
            val = match.group()

            if kind == "WHITESPACE":
                tokens.append((val, TokenType.GRAMMAR, "whitespace"))
            elif kind == "COMMENT":
                tokens.append((val, TokenType.LITERAL, "comment"))
            elif kind == "STRING":
                tokens.append((val, TokenType.LITERAL, "string"))
            elif kind == "NUMBER":
                tokens.append((val, TokenType.NUMBER, "number"))
            elif kind == "IDENTIFIER":
                if val in CODE_KEYWORDS:
                    tokens.append((val, TokenType.GRAMMAR, "keyword"))
                else:
                    tokens.append((val, TokenType.IDENTIFIER, "identifier"))
            elif kind in ("PUNCTUATION", "OPERATOR"):
                tokens.append((val, TokenType.GRAMMAR, "syntax"))
            else:
                tokens.append((val, TokenType.GRAMMAR, "other"))

        return tokens
