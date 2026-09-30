"""Pydantic data models and schemas for SecureMCP."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, computed_field


class MaskMode(str, Enum):
    """Masking policy mode defining which token classes are obfuscated."""
    CONTENT_WORDS = "content_words"   # Preserves grammar & syntax; masks nouns, content terms, numbers, entities
    ENTITIES_ONLY = "entities_only"   # Masks only recognized entities (PII, names, emails, IPs, numbers)
    CODE_AWARE = "code_aware"         # Preserves language syntax/keywords; masks identifiers, literals, secrets
    AGGRESSIVE = "aggressive"         # Obfuscates all tokens except essential structural function words


class SurrogateStrategy(str, Enum):
    """Strategy for generating synthetic surrogate tokens."""
    BRACKET = "bracket"       # e.g., [ENTITY_1], [TERM_1], [NUM_1]
    UNICODE = "unicode"       # e.g., ⟦ENT_1⟧, ⟦VAL_1⟧ (resists BPE sub-token splits)
    PSEUDOWORD = "pseudoword" # e.g., Brivon, Cranley, Velmor (natural cadence, zero perplexity spike)
    HASH = "hash"             # e.g., ~h8f2~ (compact cryptographic nonce)


class TokenType(str, Enum):
    """Linguistic and structural token classifications."""
    GRAMMAR = "grammar"         # Functional word, preposition, particle, syntax marker
    ENTITY = "entity"           # Named entity (Person, Org, Location, etc.)
    NOUN = "noun"               # Substantive noun / concept
    VERB = "verb"               # Content verb
    ADJECTIVE = "adjective"     # Descriptive adjective/adverb
    NUMBER = "number"           # Numeric literal / currency / measurement
    IDENTIFIER = "identifier"   # Code variable / function / class name
    LITERAL = "literal"         # String or character literal
    UNKNOWN = "unknown"         # General content token


class TokenMapping(BaseModel):
    """Bi-directional mapping between original token and synthetic surrogate."""
    original: str
    surrogate: str
    token_type: TokenType = TokenType.UNKNOWN
    occurrence_count: int = 1
    is_capitalized: bool = False
    is_all_caps: bool = False
    category_index: int = 1


class MaskResult(BaseModel):
    """Result returned after masking text or code."""
    masked_text: str
    session_id: str
    total_tokens: int
    masked_tokens: int
    preserved_tokens: int
    privacy_entropy_score: float = Field(
        ..., description="Information obfuscation metric between 0.0 (no masking) and 1.0 (full non-grammatical masking)"
    )
    strategy: SurrogateStrategy
    mode: MaskMode
    detected_language: str = "en"


class UnmaskResult(BaseModel):
    """Result returned after restoring original tokens from AI response."""
    unmasked_text: str
    session_id: str
    restored_tokens_count: int
    unmatched_surrogates: List[str] = Field(
        default_factory=list,
        description="Surrogate tokens found in text that had no matching session mapping",
    )

    @computed_field
    def unmasked_code(self) -> str:
        """Alias for code unmasking operations."""
        return self.unmasked_text


class SessionStats(BaseModel):
    """Sanitized session metrics that never reveal proprietary raw tokens."""
    session_id: str
    created_at: float
    last_accessed: float
    total_unique_mappings: int
    total_mask_calls: int
    total_unmask_calls: int
    mode: MaskMode
    strategy: SurrogateStrategy
    type_distribution: Dict[str, int] = Field(default_factory=dict)
