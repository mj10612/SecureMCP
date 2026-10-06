"""Session-local allocations; nonces never encode the original value."""

from __future__ import annotations

from contextlib import contextmanager
import re
import secrets
from threading import RLock
from typing import Iterator

from secure_mcp.models import SurrogateStrategy, TokenType

PREFIXES = {
    TokenType.ENTITY: "ENT",
    TokenType.NOUN: "NOUN",
    TokenType.VERB: "VERB",
    TokenType.ADJECTIVE: "ADJ",
    TokenType.NUMBER: "NUM",
    TokenType.IDENTIFIER: "ID",
    TokenType.LITERAL: "LIT",
    TokenType.UNKNOWN: "SYM",
}


def mapping_key(original: str, token_type: TokenType, code: bool = False) -> str:
    """Include type and representation context in each forward lookup."""
    return f"{'code' if code else 'text'}:{token_type.value}:{original}"


class StrategyGenerator:
    def __init__(self, strategy: SurrogateStrategy, salt: str = ""):
        self.strategy = strategy
        self.salt = salt or secrets.token_hex(32)
        self.lock = RLock()
        self.closed = False
        self.used_surrogates: set[str] = set()
        self._type_counters: dict[TokenType, int] = {}
        self._code_index = 0

    @contextmanager
    def operation(self) -> Iterator[None]:
        with self.lock:
            if self.closed:
                raise ValueError("Session has been cleared or expired.")
            yield

    @staticmethod
    def get_prefix_for_type(token_type: TokenType) -> str:
        return PREFIXES.get(token_type, "SYM")

    def generate(self, token_type: TokenType, original: str, code: bool = False) -> str:
        with self.operation():
            prefix = self.get_prefix_for_type(token_type)
            while True:
                self._type_counters[token_type] = (
                    self._type_counters.get(token_type, 0) + 1
                )
                idx = self._type_counters[token_type]
                if code:
                    self._code_index += 1
                    if token_type == TokenType.NUMBER:
                        # A native integer remains legal in match/case and SQL LIMIT positions.
                        candidate = "732846" + f"{secrets.randbelow(10**12):012d}"
                    else:
                        candidate = f"smcp_{prefix}_{self._code_index}"
                elif self.strategy == SurrogateStrategy.BRACKET:
                    candidate = f"[{prefix}_{idx}]"
                elif self.strategy == SurrogateStrategy.UNICODE:
                    candidate = f"⟦{prefix}_{idx}⟧"
                elif self.strategy == SurrogateStrategy.HASH:
                    candidate = f"~h{idx}_{secrets.token_hex(12)}~"
                else:
                    # Explicit delimiters disambiguate ordinary names and adjacent tokens.
                    syllables = ["bri", "cra", "vel", "dra", "pen", "zal", "kiv", "tha"]
                    word = "".join(
                        secrets.choice(syllables) for _ in range(6)
                    ).capitalize()
                    candidate = f"⟪{word}⟫"
                if candidate != original and candidate not in self.used_surrogates:
                    self.used_surrogates.add(candidate)
                    return candidate

    def restore_state(self, surrogates: list[str]) -> None:
        """Reserve persisted values without regenerating them."""
        self.used_surrogates.update(surrogates)
        for value in surrogates:
            match = re.fullmatch(r"(?:smcp_(?:ID|NUM|LIT)|private_symbol)_(\d+)", value)
            if match:
                self._code_index = max(self._code_index, int(match[1]))

    @staticmethod
    def get_pattern(strategy: SurrogateStrategy) -> re.Pattern[str]:
        # Candidate scans are intentionally broader than allocation syntax.
        prefix = r"(?:ENT(?:ITY)?|NOUN|VERB|ADJ|NUM(?:BER)?|ID|LIT|SYM)"
        if strategy == SurrogateStrategy.BRACKET:
            pattern = rf"\[{prefix}[-_ ]*\d+\]"
        elif strategy == SurrogateStrategy.UNICODE:
            pattern = rf"⟦{prefix}[-_ ]*\d+⟧"
        elif strategy == SurrogateStrategy.HASH:
            pattern = r"~h\d+[-_ ]+[a-f0-9]+~"
        else:
            pattern = r"⟪[^⟪⟫\n]+⟫"
        return re.compile(
            pattern
            + r"|\b(?:smcp_(?:ID|NUM|LIT)|private_symbol)_\d+\b|\b732846\d{12}\b",
            re.IGNORECASE,
        )
