"""Core bi-directional semantic masking and unmasking engine for SecureMCP."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from secure_mcp.engine.code_parser import CodeParser
from secure_mcp.engine.grammar_en import EnglishGrammarEngine
from secure_mcp.engine.grammar_ko import KoreanGrammarEngine, is_hangul_string
from secure_mcp.engine.strategies import StrategyGenerator
from secure_mcp.engine.tokenizer import MultilingualTokenizer, TextChunk
from secure_mcp.models import (
    MaskMode,
    MaskResult,
    SurrogateStrategy,
    TokenMapping,
    TokenType,
    UnmaskResult,
)


class MaskingEngine:
    """Zero-knowledge grammar-preserving masking and restoration engine."""

    def __init__(self):
        self.en_grammar = EnglishGrammarEngine()
        self.ko_grammar = KoreanGrammarEngine()
        self.code_parser = CodeParser()
        self.tokenizer = MultilingualTokenizer()

    def mask_text(
        self,
        text: str,
        session_id: str,
        generator: StrategyGenerator,
        mapping_store: Dict[str, TokenMapping],
        reverse_store: Dict[str, TokenMapping],
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        custom_preserve: Optional[Set[str]] = None,
        language: str = "auto",
    ) -> MaskResult:
        """Mask non-grammatical content words while strictly preserving grammatical syntax."""
        custom_preserve = custom_preserve or set()
        chunks = self.tokenizer.tokenize(text)

        output_parts: List[str] = []
        total_tokens = 0
        masked_tokens = 0
        preserved_tokens = 0

        # Auto-detect language if needed
        is_korean = (language == "ko") or (language == "auto" and is_hangul_string(text))

        for i, chunk in enumerate(chunks):
            if chunk.is_whitespace or chunk.is_punctuation:
                output_parts.append(chunk.text)
                continue

            total_tokens += 1
            word = chunk.text

            # Handle atomic numbers and currencies ($43,000,000, 15.5%, etc.)
            if chunk.is_number:
                surrogate = self._get_or_create_surrogate(
                    word, TokenType.NUMBER, generator, mapping_store, reverse_store
                )
                output_parts.append(surrogate)
                masked_tokens += 1
                continue

            # Check custom preserved terms
            if word.lower() in custom_preserve or word in custom_preserve:
                output_parts.append(word)
                preserved_tokens += 1
                continue

            # Korean word processing
            if is_korean and is_hangul_string(word):
                stem, suffix, token_type = self.ko_grammar.decompose_token(word)

                if token_type == TokenType.GRAMMAR or (mode == MaskMode.ENTITIES_ONLY and token_type != TokenType.ENTITY):
                    output_parts.append(word)
                    preserved_tokens += 1
                    continue

                surrogate = self._get_or_create_surrogate(
                    stem, token_type, generator, mapping_store, reverse_store
                )
                output_parts.append(surrogate + suffix)
                masked_tokens += 1
                continue

            # English / Latin word processing
            is_sentence_start = (i == 0 or (i > 1 and chunks[i - 2].text in (".", "!", "?", "\n")))
            token_type, should_mask = self.en_grammar.classify_token(word, is_sentence_start=is_sentence_start)

            if not should_mask or (mode == MaskMode.ENTITIES_ONLY and token_type not in (TokenType.ENTITY, TokenType.NUMBER)):
                output_parts.append(word)
                preserved_tokens += 1
                continue

            # Handle possessives like Alice's -> [ENT_1]'s
            possessive_suffix = ""
            base_word = word
            if "'" in word:
                parts = word.split("'", 1)
                base_word = parts[0]
                possessive_suffix = "'" + parts[1]

            surrogate = self._get_or_create_surrogate(
                base_word, token_type, generator, mapping_store, reverse_store
            )
            output_parts.append(surrogate + possessive_suffix)
            masked_tokens += 1

        privacy_score = round(masked_tokens / max(1, total_tokens), 4)

        return MaskResult(
            masked_text="".join(output_parts),
            session_id=session_id,
            total_tokens=total_tokens,
            masked_tokens=masked_tokens,
            preserved_tokens=preserved_tokens,
            privacy_entropy_score=privacy_score,
            strategy=strategy,
            mode=mode,
            detected_language="ko" if is_korean else "en",
        )

    def mask_code(
        self,
        code: str,
        session_id: str,
        generator: StrategyGenerator,
        mapping_store: Dict[str, TokenMapping],
        reverse_store: Dict[str, TokenMapping],
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
    ) -> MaskResult:
        """Mask code identifiers and string literals while keeping keywords, operators, and syntax."""
        tokens = self.code_parser.tokenize(code)
        output_parts: List[str] = []
        total_tokens = 0
        masked_tokens = 0
        preserved_tokens = 0

        for raw_val, token_type, subkind in tokens:
            if subkind == "whitespace":
                output_parts.append(raw_val)
                continue

            total_tokens += 1

            if token_type == TokenType.GRAMMAR:
                output_parts.append(raw_val)
                preserved_tokens += 1
            elif token_type == TokenType.IDENTIFIER:
                surrogate = self._get_or_create_surrogate(
                    raw_val, TokenType.IDENTIFIER, generator, mapping_store, reverse_store
                )
                output_parts.append(surrogate)
                masked_tokens += 1
            elif token_type == TokenType.LITERAL:
                # Mask string content inside quotes
                if raw_val.startswith(('"""', "'''", '"', "'")):
                    quote_char = raw_val[:3] if raw_val.startswith(('"""', "'''")) else raw_val[0]
                    content = raw_val[len(quote_char):-len(quote_char)] if len(raw_val) >= 2 * len(quote_char) else ""
                    if content:
                        surrogate = self._get_or_create_surrogate(
                            content, TokenType.LITERAL, generator, mapping_store, reverse_store
                        )
                        output_parts.append(f"{quote_char}{surrogate}{quote_char}")
                    else:
                        output_parts.append(raw_val)
                else:
                    output_parts.append(raw_val)
                masked_tokens += 1
            elif token_type == TokenType.NUMBER:
                surrogate = self._get_or_create_surrogate(
                    raw_val, TokenType.NUMBER, generator, mapping_store, reverse_store
                )
                output_parts.append(surrogate)
                masked_tokens += 1
            else:
                output_parts.append(raw_val)
                preserved_tokens += 1

        privacy_score = round(masked_tokens / max(1, total_tokens), 4)

        return MaskResult(
            masked_text="".join(output_parts),
            session_id=session_id,
            total_tokens=total_tokens,
            masked_tokens=masked_tokens,
            preserved_tokens=preserved_tokens,
            privacy_entropy_score=privacy_score,
            strategy=strategy,
            mode=MaskMode.CODE_AWARE,
            detected_language="code",
        )

    def unmask(
        self,
        masked_text: str,
        session_id: str,
        reverse_store: Dict[str, TokenMapping],
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
    ) -> UnmaskResult:
        """Restore original text by substituting synthetic surrogate tokens with original values."""
        if not reverse_store:
            return UnmaskResult(
                unmasked_text=masked_text,
                session_id=session_id,
                restored_tokens_count=0,
                unmatched_surrogates=[],
            )

        # Sort surrogates longest first to prevent prefix substitution collisions
        sorted_surrogates = sorted(reverse_store.keys(), key=len, reverse=True)
        restored_text = masked_text
        restored_count = 0

        for surrogate in sorted_surrogates:
            if surrogate in restored_text:
                mapping = reverse_store[surrogate]
                occurrences = restored_text.count(surrogate)
                restored_text = restored_text.replace(surrogate, mapping.original)
                restored_count += occurrences

        # Scan for any orphan surrogate tokens left in text
        pattern = StrategyGenerator.get_pattern(strategy)
        remaining = pattern.findall(restored_text)
        unmatched = [tok for tok in remaining if tok not in reverse_store]

        return UnmaskResult(
            unmasked_text=restored_text,
            session_id=session_id,
            restored_tokens_count=restored_count,
            unmatched_surrogates=list(set(unmatched)),
        )

    def _get_or_create_surrogate(
        self,
        original_token: str,
        token_type: TokenType,
        generator: StrategyGenerator,
        mapping_store: Dict[str, TokenMapping],
        reverse_store: Dict[str, TokenMapping],
    ) -> str:
        """Lookup existing surrogate or generate a deterministic new one."""
        key = original_token.strip()
        if key in mapping_store:
            mapping = mapping_store[key]
            mapping.occurrence_count += 1
            return mapping.surrogate

        surrogate = generator.generate(token_type, key)
        mapping = TokenMapping(
            original=key,
            surrogate=surrogate,
            token_type=token_type,
            occurrence_count=1,
            is_capitalized=key.istitle(),
            is_all_caps=key.isupper() and len(key) > 1,
        )
        mapping_store[key] = mapping
        reverse_store[surrogate] = mapping
        return surrogate
