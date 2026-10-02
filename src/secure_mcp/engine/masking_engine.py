"""Local masking and exact, single-pass restoration."""

from __future__ import annotations

from contextlib import nullcontext
import re

from secure_mcp.engine.code_parser import CodeParser, detect_language, literal_parts
from secure_mcp.engine.grammar_en import EnglishGrammarEngine
from secure_mcp.engine.grammar_ko import (
    KoreanGrammarEngine,
    is_hangul_string,
    normalize_particle,
)
from secure_mcp.engine.strategies import StrategyGenerator, mapping_key
from secure_mcp.engine.tokenizer import MultilingualTokenizer, TextChunk
from secure_mcp.models import (
    MaskMode,
    MaskResult,
    SurrogateStrategy,
    TokenMapping,
    TokenType,
    UnmaskResult,
)

STRUCTURAL_WORDS = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "to",
    "for",
    "with",
    "on",
    "at",
    "from",
    "by",
    "and",
    "or",
    "but",
    "if",
}
STRUCTURAL_KO = {"그리고", "그러나", "및", "또는", "하지만"}


class MaskingEngine:
    def __init__(self) -> None:
        self.en_grammar = EnglishGrammarEngine()
        self.ko_grammar = KoreanGrammarEngine()
        self.code_parser = CodeParser()
        self.tokenizer = MultilingualTokenizer()

    @staticmethod
    def _strategy(
        generator: StrategyGenerator, requested: SurrogateStrategy | None
    ) -> SurrogateStrategy:
        if requested is not None and requested != generator.strategy:
            raise ValueError("Requested strategy must match the generator's strategy.")
        return generator.strategy

    def mask_text(
        self,
        text: str,
        session_id: str,
        generator: StrategyGenerator,
        mapping_store: dict[str, TokenMapping],
        reverse_store: dict[str, TokenMapping],
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy | None = None,
        custom_preserve: set[str] | None = None,
        language: str = "auto",
        sensitive_terms: set[str] | None = None,
        code_language: str = "auto",
    ) -> MaskResult:
        strategy = self._strategy(generator, strategy)
        if language not in {"auto", "en", "ko"}:
            raise ValueError("language must be auto, en, or ko")
        with generator.operation():
            if mode == MaskMode.CODE_AWARE:
                return self.mask_code(
                    text,
                    session_id,
                    generator,
                    mapping_store,
                    reverse_store,
                    strategy,
                    language=code_language,
                )
            terms = sensitive_terms or set()
            preserve = {word.casefold() for word in (custom_preserve or set())}
            chunks = self._chunks(text, terms)
            output: list[str] = []
            total = masked = 0
            for chunk in chunks:
                word = chunk.text
                if chunk.is_whitespace or (
                    not chunk.is_word and not chunk.is_code and chunk.kind != "ENTITY"
                ):
                    output.append(word)
                    continue
                total += 1
                stem, suffix = word, ""
                force = chunk.kind in {
                    "ENTITY",
                    "EMAIL",
                    "URL",
                    "SECRET",
                    "UUID",
                    "IP",
                    "PHONE",
                    "CODE_BLOCK",
                }
                token_type = TokenType.ENTITY if force else TokenType.NOUN
                should_mask = force
                if chunk.is_number:
                    token_type, should_mask = TokenType.NUMBER, True
                elif not force and is_hangul_string(word):
                    # Mixed Korean/English input is handled per token, even with language=en.
                    known = terms | {m.original for m in mapping_store.values()}
                    stem, suffix, token_type = self.ko_grammar.decompose_token(
                        word, known
                    )
                    should_mask = token_type != TokenType.GRAMMAR
                    # Case-free scripts have no reliable proper-name cue: mask conservatively.
                    if (
                        mode == MaskMode.ENTITIES_ONLY
                        and should_mask
                        and token_type != TokenType.NUMBER
                    ):
                        token_type = TokenType.ENTITY
                    if mode == MaskMode.AGGRESSIVE and word not in STRUCTURAL_KO:
                        stem, suffix, token_type, should_mask = (
                            word,
                            "",
                            TokenType.NOUN,
                            True,
                        )
                elif not force:
                    token_type, should_mask = self.en_grammar.classify_token(word)
                    if (
                        any(ord(c) > 127 and c.isalpha() for c in word)
                        and not word.isascii()
                    ):
                        if (
                            mode == MaskMode.ENTITIES_ONLY
                            and token_type != TokenType.GRAMMAR
                        ):
                            token_type, should_mask = TokenType.ENTITY, True
                    if mode == MaskMode.ENTITIES_ONLY and token_type not in {
                        TokenType.ENTITY,
                        TokenType.NUMBER,
                    }:
                        should_mask = False
                    if (
                        mode == MaskMode.AGGRESSIVE
                        and word.casefold() not in STRUCTURAL_WORDS
                    ):
                        should_mask = True
                    if should_mask and word.endswith(("'s", "’s")):
                        stem, suffix = word[:-2], word[-2:]
                if word.casefold() in preserve and not force:
                    should_mask = False
                if should_mask:
                    output.append(
                        self._get_or_create_surrogate(
                            stem, token_type, generator, mapping_store, reverse_store
                        )
                        + suffix
                    )
                    masked += 1
                else:
                    output.append(word)
            return self._result(
                "".join(output),
                session_id,
                total,
                masked,
                strategy,
                mode,
                "ko" if is_hangul_string(text) else "en",
            )

    def _chunks(self, text: str, terms: set[str]) -> list[TextChunk]:
        if not terms:
            return self.tokenizer.tokenize(text)
        if "" in terms:
            raise ValueError("Sensitive terms must be nonempty.")
        pattern = re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(s) for s in sorted(terms, key=len, reverse=True))
            + r")(?!\w)"
        )
        output: list[TextChunk] = []
        cursor = 0
        for match in pattern.finditer(text):
            output.extend(self.tokenizer.tokenize(text[cursor : match.start()]))
            output.append(TextChunk(match[0], "ENTITY"))
            cursor = match.end()
        output.extend(self.tokenizer.tokenize(text[cursor:]))
        return output

    def mask_code(
        self,
        code: str,
        session_id: str,
        generator: StrategyGenerator,
        mapping_store: dict[str, TokenMapping],
        reverse_store: dict[str, TokenMapping],
        strategy: SurrogateStrategy | None = None,
        language: str = "auto",
    ) -> MaskResult:
        strategy = self._strategy(generator, strategy)
        language = detect_language(code, language)
        with generator.operation():
            tokens = self.code_parser.tokenize(code, language)
            output: list[str] = []
            total = masked = 0
            for raw, token_type, subkind in tokens:
                if subkind == "whitespace":
                    output.append(raw)
                    continue
                if subkind == "syntax":
                    output.append(raw)
                    continue
                total += 1
                if token_type == TokenType.GRAMMAR:
                    output.append(raw)
                elif token_type == TokenType.LITERAL:
                    prefix, suffix = literal_parts(raw, subkind)
                    end = len(raw) - len(suffix) if suffix else len(raw)
                    content = raw[len(prefix) : end]
                    if content:
                        surrogate = self._get_or_create_surrogate(
                            content,
                            token_type,
                            generator,
                            mapping_store,
                            reverse_store,
                            code=True,
                        )
                        output.append(prefix + surrogate + suffix)
                        masked += 1
                    else:
                        output.append(raw)
                else:
                    output.append(
                        self._get_or_create_surrogate(
                            raw,
                            token_type,
                            generator,
                            mapping_store,
                            reverse_store,
                            code=True,
                        )
                    )
                    masked += 1
            result = self._result(
                "".join(output),
                session_id,
                total,
                masked,
                strategy,
                MaskMode.CODE_AWARE,
                "code",
            )
            result.code_language = language
            return result

    @staticmethod
    def _result(
        text: str,
        sid: str,
        total: int,
        masked: int,
        strategy: SurrogateStrategy,
        mode: MaskMode,
        language: str,
    ) -> MaskResult:
        return MaskResult(
            masked_text=text,
            session_id=sid,
            total_tokens=total,
            masked_tokens=masked,
            preserved_tokens=total - masked,
            privacy_entropy_score=round(masked / max(1, total), 4),
            strategy=strategy,
            mode=mode,
            detected_language=language,
        )

    def unmask(
        self,
        masked_text: str,
        session_id: str,
        reverse_store: dict[str, TokenMapping],
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        normalize_particles: bool = False,
        strict: bool = False,
    ) -> UnmaskResult:
        generator = getattr(reverse_store, "generator", None)
        guard = generator.operation() if generator else nullcontext()
        with guard:
            sorted_values = sorted(reverse_store, key=len, reverse=True)
            alternatives = []
            for value in sorted_values:
                escaped = re.escape(value)
                if value[0].isalnum():
                    escaped = r"(?<![A-Za-z0-9_])" + escaped + r"(?![A-Za-z0-9_])"
                alternatives.append(escaped)
            count = 0
            unique: set[str] = set()

            def restore(match: re.Match[str]) -> str:
                nonlocal count
                count += 1
                key = match[1] if normalize_particles else match[0]
                mapping = reverse_store[key]
                unique.add(key)
                suffix = match[2] or "" if normalize_particles else ""
                return mapping.original + (
                    normalize_particle(mapping.original, suffix) if suffix else ""
                )

            output = masked_text
            if alternatives:
                pattern = "|".join(alternatives)
                if normalize_particles:
                    pattern = (
                        "("
                        + pattern
                        + r")((?:으로|이랑|이나|은|는|을|를|과|와|이|가|로|랑|나)(?:부터|도|만|는|의)?(?=\s|[.,!?]|$))?"
                    )
                output = re.sub(pattern, restore, masked_text)
            candidates = StrategyGenerator.get_pattern(strategy).findall(masked_text)
            unmatched = sorted({c for c in candidates if c not in reverse_store})
            if strict and unmatched:
                raise ValueError(
                    "Unrecognized or altered surrogate tokens: " + ", ".join(unmatched)
                )
            return UnmaskResult(
                unmasked_text=output,
                session_id=session_id,
                restored_tokens_count=count,
                restored_unique_tokens=len(unique),
                unmatched_surrogates=unmatched,
            )

    def _get_or_create_surrogate(
        self,
        original_token: str,
        token_type: TokenType,
        generator: StrategyGenerator,
        mapping_store: dict[str, TokenMapping],
        reverse_store: dict[str, TokenMapping],
        code: bool = False,
    ) -> str:
        with generator.operation():
            key = mapping_key(original_token, token_type, code)
            if key in mapping_store:
                mapping_store[key].occurrence_count += 1
                return mapping_store[key].surrogate
            surrogate = generator.generate(token_type, original_token, code=code)
            mapping = TokenMapping(
                original=original_token,
                surrogate=surrogate,
                token_type=token_type,
                context="code" if code else "text",
                is_capitalized=original_token.istitle(),
                is_all_caps=original_token.isupper() and len(original_token) > 1,
            )
            mapping_store[key] = mapping
            reverse_store[surrogate] = mapping
            return surrogate
