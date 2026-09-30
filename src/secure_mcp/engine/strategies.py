"""Surrogate token generation strategies for SecureMCP.

Provides distinct strategies:
- BRACKET: Explicit structured tags (e.g., [ORG_1], [TERM_2], [NUM_1])
- UNICODE: Mathematical bracket enclosures (e.g., ⟦ORG_1⟧) that resist BPE sub-word splitting
- PSEUDOWORD: Phonotactically natural artificial words (e.g., Brivon, Cranley, Velmor)
- HASH: Compact cryptographic nonces (e.g., ~h7a1~)
"""

from __future__ import annotations

import hashlib
import re
from typing import Set
from secure_mcp.models import SurrogateStrategy, TokenType


# High-frequency phonotactically pleasant syllables for pseudoword synthesis
_ONSET = ["b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p", "r", "s", "t", "v", "w", "z", "br", "cr", "dr", "fr", "gr", "pr", "tr", "st", "sp", "pl", "cl", "fl"]
_NUCLEUS = ["a", "e", "i", "o", "u", "ar", "or", "er", "el", "an", "on", "in"]
_CODA = ["n", "r", "s", "t", "l", "m", "p", "k", "x", "nd", "nt", "rt", "rk", "ld", "st", "th"]

# Curated list of distinct, pronounceable base pseudowords
_CURATED_PSEUDOWORDS = [
    "Brivon", "Cranley", "Velmor", "Draxen", "Pendor", "Zalor", "Kivian", "Tharok", "Morven", "Solix",
    "Belgor", "Calyx", "Danver", "Elrion", "Fenris", "Galdor", "Harvik", "Ivoran", "Jareth", "Kaelen",
    "Lorien", "Myrton", "Novak", "Olynn", "Praxis", "Quorin", "Raelis", "Sythor", "Talren", "Ulric",
    "Vaelen", "Wynter", "Xylar", "Yorick", "Zenth", "Aerith", "Boron", "Corvan", "Delrik", "Eryx",
    "Faulcon", "Gellor", "Halden", "Ilthor", "Jondar", "Kelmar", "Lyron", "Maelor", "Nydor", "Orlan",
    "Pyrak", "Quaris", "Rhogar", "Selvan", "Tiber", "Unkar", "Vondar", "Wexler", "Xerox", "Yarden",
    "Zarok", "Austen", "Barrek", "Casper", "Darius", "Edron", "Fulmar", "Garrick", "Hobart", "Isidor",
    "Jansen", "Kragan", "Lorgan", "Merek", "Nestor", "Osric", "Parlan", "Quade", "Rastan", "Savik",
    "Torgan", "Uthor", "Varek", "Waldron", "Xander", "Yorken", "Zephyr", "Alden", "Braxton", "Corin"
]


class StrategyGenerator:
    """Generates synthetic surrogates according to chosen strategy and tracks allocations."""

    def __init__(self, strategy: SurrogateStrategy, salt: str = ""):
        self.strategy = strategy
        self.salt = salt
        self.used_surrogates: Set[str] = set()
        self._type_counters: dict[TokenType, int] = {}
        self._global_index = 0

    def get_prefix_for_type(self, token_type: TokenType) -> str:
        """Map token types to standard category labels."""
        mapping = {
            TokenType.ENTITY: "ENT",
            TokenType.NOUN: "NOUN",
            TokenType.VERB: "VERB",
            TokenType.ADJECTIVE: "ADJ",
            TokenType.NUMBER: "NUM",
            TokenType.IDENTIFIER: "ID",
            TokenType.LITERAL: "LIT",
            TokenType.UNKNOWN: "SYM",
        }
        return mapping.get(token_type, "SYM")

    def generate(self, token_type: TokenType, original: str) -> str:
        """Generate a new unique surrogate for a given token type and original word."""
        self._type_counters[token_type] = self._type_counters.get(token_type, 0) + 1
        idx = self._type_counters[token_type]
        self._global_index += 1

        prefix = self.get_prefix_for_type(token_type)

        if self.strategy == SurrogateStrategy.BRACKET:
            candidate = f"[{prefix}_{idx}]"
            while candidate in self.used_surrogates:
                self._type_counters[token_type] += 1
                idx = self._type_counters[token_type]
                candidate = f"[{prefix}_{idx}]"
            self.used_surrogates.add(candidate)
            return candidate

        elif self.strategy == SurrogateStrategy.UNICODE:
            candidate = f"⟦{prefix}_{idx}⟧"
            while candidate in self.used_surrogates:
                self._type_counters[token_type] += 1
                idx = self._type_counters[token_type]
                candidate = f"⟦{prefix}_{idx}⟧"
            self.used_surrogates.add(candidate)
            return candidate

        elif self.strategy == SurrogateStrategy.PSEUDOWORD:
            # Pick from curated list or construct synthetic pronounceable word
            if idx <= len(_CURATED_PSEUDOWORDS):
                candidate = _CURATED_PSEUDOWORDS[idx - 1]
            else:
                o_idx = (idx * 7) % len(_ONSET)
                n_idx = (idx * 11) % len(_NUCLEUS)
                c_idx = (idx * 13) % len(_CODA)
                candidate = f"{_ONSET[o_idx].capitalize()}{_NUCLEUS[n_idx]}{_CODA[c_idx]}{idx}"
            
            counter = 1
            base = candidate
            while candidate in self.used_surrogates:
                candidate = f"{base}_{counter}"
                counter += 1
            self.used_surrogates.add(candidate)
            return candidate

        elif self.strategy == SurrogateStrategy.HASH:
            # Deterministic salted hash
            hash_input = f"{self.salt}:{prefix}:{idx}:{original}".encode("utf-8")
            digest = hashlib.sha256(hash_input).hexdigest()[:6]
            candidate = f"~h{idx}_{digest[:4]}~"
            while candidate in self.used_surrogates:
                idx += 1
                candidate = f"~h{idx}_{digest[:4]}~"
            self.used_surrogates.add(candidate)
            return candidate

        # Default fallback
        candidate = f"[{prefix}_{idx}]"
        self.used_surrogates.add(candidate)
        return candidate

    @staticmethod
    def get_pattern(strategy: SurrogateStrategy) -> re.Pattern:
        """Returns compiled regex pattern for finding surrogate tokens in text."""
        if strategy == SurrogateStrategy.BRACKET:
            return re.compile(r'\[[A-Z0-9_]+\]')
        elif strategy == SurrogateStrategy.UNICODE:
            return re.compile(r'⟦[A-Z0-9_]+⟧')
        elif strategy == SurrogateStrategy.HASH:
            return re.compile(r'~h[0-9]+_[a-f0-9]+~')
        elif strategy == SurrogateStrategy.PSEUDOWORD:
            # Word boundary regex for pseudowords (lookup against session table)
            return re.compile(r'\b[A-Z][a-zA-Z0-9_]*\b')
        return re.compile(r'\[[A-Z0-9_]+\]')
