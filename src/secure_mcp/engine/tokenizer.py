"""Multilingual text tokenizer preserving whitespace, punctuation, and layout fidelity."""

from __future__ import annotations

import re
from typing import List


# Regex pattern splitting text while preserving delimiters and whitespace
TOKEN_SPLIT_REGEX = re.compile(
    r'(?P<WHITESPACE>\s+)'
    r'|(?P<CODE_BLOCK>```[\s\S]*?```|`[^`\n]+`)'
    r'|(?P<URL>https?://[^\s<>"\'{}|\\^`]+)'
    r'|(?P<EMAIL>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})'
    r'|(?P<NUMBER>[\$€£₩¥]?\d+(?:,\d{3})*(?:\.\d+)?%?)'
    r'|(?P<HANGUL_WORD>[가-힣0-9A-Za-z_]+)'
    r'|(?P<LATIN_WORD>[A-Za-z0-9_]+(?:\'[a-zA-Z]+)?)'
    r'|(?P<PUNCTUATION>[.,!?;:\"\'\(\)\[\]\{\}—–~`@#\$%\^&\*\+=\<\>/\\\|])'
    r'|(?P<OTHER>.)'
)


class TextChunk:
    """Represents a text segment with its syntactic character."""
    def __init__(self, text: str, kind: str):
        self.text = text
        self.kind = kind

    @property
    def is_word(self) -> bool:
        return self.kind in ("HANGUL_WORD", "LATIN_WORD", "EMAIL", "URL", "NUMBER")

    @property
    def is_number(self) -> bool:
        return self.kind == "NUMBER"

    @property
    def is_code(self) -> bool:
        return self.kind == "CODE_BLOCK"

    @property
    def is_whitespace(self) -> bool:
        return self.kind == "WHITESPACE"

    @property
    def is_punctuation(self) -> bool:
        return self.kind == "PUNCTUATION"


class MultilingualTokenizer:
    """Tokenizes text preserving full character-level reconstruction fidelity."""

    @staticmethod
    def tokenize(text: str) -> List[TextChunk]:
        """Split text into structured chunks preserving all spacing and punctuation."""
        chunks: List[TextChunk] = []
        for match in TOKEN_SPLIT_REGEX.finditer(text):
            kind = match.lastgroup or "OTHER"
            val = match.group()
            chunks.append(TextChunk(text=val, kind=kind))
        return chunks
