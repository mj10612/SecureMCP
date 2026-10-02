"""English grammar, syntax preservation, and token classification engine."""

from __future__ import annotations

import re
from typing import Set, Tuple
from secure_mcp.models import TokenType
from secure_mcp.engine.tokenizer import (
    SECRET_PATTERN,
    PHONE_PATTERN,
    IP_PATTERN,
    UUID_PATTERN,
)


# Comprehensive closed-class English grammatical words
CLOSED_CLASS_WORDS: Set[str] = {
    # Determiners & Articles
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "every",
    "each",
    "any",
    "some",
    "all",
    "no",
    "neither",
    "either",
    "both",
    "another",
    "such",
    "much",
    "many",
    "few",
    "fewer",
    "several",
    "enough",
    "other",
    # Prepositions (Comprehensive)
    "of",
    "in",
    "to",
    "for",
    "with",
    "on",
    "at",
    "from",
    "by",
    "about",
    "as",
    "into",
    "like",
    "through",
    "after",
    "over",
    "between",
    "out",
    "against",
    "during",
    "without",
    "before",
    "under",
    "around",
    "among",
    "across",
    "throughout",
    "towards",
    "toward",
    "upon",
    "within",
    "along",
    "down",
    "behind",
    "beyond",
    "beside",
    "near",
    "off",
    "onto",
    "since",
    "until",
    "via",
    "past",
    "amid",
    "beneath",
    "up",
    "regarding",
    "concerning",
    "despite",
    "except",
    # Conjunctions
    "and",
    "but",
    "or",
    "nor",
    "so",
    "yet",
    "because",
    "although",
    "though",
    "while",
    "whereas",
    "if",
    "unless",
    "since",
    "until",
    "whether",
    "than",
    "whenever",
    "wherever",
    "once",
    # Pronouns & Reflexives
    "i",
    "me",
    "my",
    "mine",
    "myself",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "we",
    "us",
    "our",
    "ours",
    "ourselves",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    "who",
    "whom",
    "whose",
    "which",
    "what",
    "whatever",
    "whoever",
    "whomever",
    "someone",
    "anyone",
    "everyone",
    "no one",
    "nobody",
    "somebody",
    "anybody",
    "something",
    "anything",
    "everything",
    "nothing",
    "one",
    "ones",
    # Auxiliary & Modal Verbs
    "is",
    "am",
    "are",
    "was",
    "were",
    "be",
    "being",
    "been",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "done",
    "doing",
    "will",
    "would",
    "shall",
    "should",
    "may",
    "might",
    "must",
    "can",
    "could",
    "ought",
    # Functional Adverbs & Particles
    "not",
    "n't",
    "here",
    "there",
    "where",
    "when",
    "why",
    "how",
    "very",
    "too",
    "also",
    "just",
    "only",
    "now",
    "then",
    "still",
    "already",
    "even",
    "ever",
    "never",
    "always",
    "often",
    "sometimes",
    "usually",
    "almost",
    "quite",
    "rather",
    "well",
    "else",
    "more",
    "most",
    "less",
    "least",
}

# Regex patterns for entities and sensitive data
RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
RE_PHONE = re.compile(PHONE_PATTERN)
RE_IP = re.compile(IP_PATTERN)
RE_NUMBER = re.compile(r"^[\$€£₩¥]?\d+(?:,\d{3})*(?:\.\d+)?%?$")
RE_UUID = re.compile(UUID_PATTERN)
RE_SECRET_KEY = re.compile(SECRET_PATTERN)


class EnglishGrammarEngine:
    """Classifies English tokens into grammatical vs content units."""

    @staticmethod
    def is_grammatical(word: str) -> bool:
        """Return True if word is a closed-class grammatical function word."""
        clean = word.lower().strip(".,!?;:\"'()[]{}")
        return clean in CLOSED_CLASS_WORDS

    @staticmethod
    def classify_token(word: str) -> Tuple[TokenType, bool]:
        """Classify a token into TokenType and whether it should be masked."""
        clean = word.strip()
        if clean.endswith("'s"):
            clean = clean[:-2]

        # Check sensitive patterns first
        if (
            RE_EMAIL.match(clean)
            or RE_PHONE.match(clean)
            or RE_IP.match(clean)
            or RE_UUID.match(clean)
            or RE_SECRET_KEY.match(clean)
        ):
            return TokenType.ENTITY, True

        # Check numeric patterns
        if RE_NUMBER.match(clean):
            return TokenType.NUMBER, True

        # Check closed-class grammar words
        if EnglishGrammarEngine.is_grammatical(clean):
            return TokenType.GRAMMAR, False

        # Capitalization is a conservative entity cue, including sentence starts.
        if clean.istitle() and len(clean) > 1:
            return TokenType.ENTITY, True

        # All-caps token (e.g. acronyms, organizations)
        if clean.isupper() and len(clean) > 1:
            return TokenType.ENTITY, True

        # Common noun / verb / adjective
        return TokenType.NOUN, True
