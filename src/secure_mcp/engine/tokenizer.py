"""Unicode words and sensitive spans, before ordinary word splitting."""

from __future__ import annotations

import re
import unicodedata

SECRET_PATTERN = (
    r"\b(?:(?:sk-|ak-|gh[pousr]_|xox[bpa]-|xapp-|sec_|glpat-|github_pat_|"
    r"[sr]k_(?:live|test)_|AIza|AKIA|ASIA)[A-Za-z0-9_-]{10,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|(?=[A-Za-z0-9/+=_-]{24,}\b)(?=[A-Za-z0-9/+=_-]*[A-Z])"
    r"(?=[A-Za-z0-9/+=_-]*[a-z])[A-Za-z0-9/+=_-]{24,})"
)
UUID_PATTERN = r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b"
IP_PATTERN = (
    r"(?<!\w)(?:(?:\d{1,3}\.){3}\d{1,3}"
    r"|(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]{0,39})(?!\w)"
)
PHONE_PATTERN = (
    r"(?<!\w)(?:\+\d{1,3}[-. ]?)?(?:\(\d{2,4}\)|\d{2,4})[-. ]\d{3,4}[-. ]\d{4}(?!\w)"
)
TOKEN_SPLIT_REGEX = re.compile(
    r"(?P<WHITESPACE>\s+)"
    r"|(?P<CODE_BLOCK>```[\s\S]*?```|`[^`\n]+`)"
    r"|(?P<URL>(?:https?|s3|ftp)://[^\s<>\"'{}|\\^`]+)"
    r"|(?P<EMAIL>[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,})"
    rf"|(?P<SECRET>{SECRET_PATTERN})"
    rf"|(?P<UUID>{UUID_PATTERN})"
    rf"|(?P<IP>{IP_PATTERN})"
    rf"|(?P<PHONE>{PHONE_PATTERN})"
    r"|(?P<HANGUL_WORD>(?=[\w]*[가-힣])\w+)"
    r"|(?P<NUMBER>[$€£₩¥]?\d+(?:,\d{3})*(?:\.\d+)?%?(?:[A-Za-z]+)?)"
    r"|(?P<WORD>\w+(?:['’]\w+)*)"
    r"|(?P<OTHER>.)",
    re.UNICODE,
)


class TextChunk:
    def __init__(self, text: str, kind: str):
        self.text = text
        self.kind = kind

    @property
    def is_word(self) -> bool:
        return self.kind in {
            "WORD",
            "HANGUL_WORD",
            "EMAIL",
            "URL",
            "SECRET",
            "UUID",
            "IP",
            "PHONE",
            "NUMBER",
        }

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
        return self.kind == "OTHER" and all(
            unicodedata.category(c)[0] in "PS" for c in self.text
        )


class MultilingualTokenizer:
    @staticmethod
    def tokenize(text: str) -> list[TextChunk]:
        return [
            TextChunk(m[0], m.lastgroup or "OTHER")
            for m in TOKEN_SPLIT_REGEX.finditer(text)
        ]
