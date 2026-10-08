"""Single-pass restoration must not interpret restored originals as aliases."""

import pytest

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.models import TokenMapping, TokenType
from secure_mcp.session import PrivacySession


@pytest.mark.parametrize("normalize_particles", [False, True])
def test_numeric_original_alias_is_restored_once(normalize_particles):
    engine = MaskingEngine()
    session = PrivacySession("numeric-cascade")

    def mask(source):
        return engine.mask_code(
            source,
            session.session_id,
            session.generator,
            session.forward_store,
            session.reverse_store,
            language="python",
        ).masked_text

    original_alias = mask("42")
    new_alias = mask(original_alias)
    result = engine.unmask(
        new_alias,
        session.session_id,
        session.reverse_store,
        normalize_particles=normalize_particles,
        strict=True,
    )
    assert result.unmasked_text == original_alias
    assert result.restored_tokens_count == result.restored_unique_tokens == 1


@pytest.mark.parametrize("normalize_particles", [False, True])
def test_glued_numeric_runs_do_not_cascade_and_report_unknown(normalize_particles):
    first, second, unknown = ("732846" + f"{i:012d}" for i in (1, 2, 3))
    mappings = {
        first: TokenMapping(
            original="[NOUN_1]", surrogate=first, token_type=TokenType.NUMBER
        ),
        second: TokenMapping(
            original="7", surrogate=second, token_type=TokenType.NUMBER
        ),
        "[NOUN_1]": TokenMapping(
            original="사과", surrogate="[NOUN_1]", token_type=TokenType.NOUN
        ),
    }
    text = f"{first}{second}{unknown} [NOUN_1]이"
    result = MaskingEngine().unmask(
        text,
        "numeric-cascade",
        mappings,
        normalize_particles=normalize_particles,
    )
    assert (
        result.unmasked_text
        == f"[NOUN_1]7{unknown} 사과{'가' if normalize_particles else '이'}"
    )
    assert result.restored_tokens_count == result.restored_unique_tokens == 3
    assert result.unmatched_surrogates == [unknown]
    with pytest.raises(ValueError, match="Unrecognized"):
        MaskingEngine().unmask(text, "numeric-cascade", mappings, strict=True)


def test_foreign_digit_runs_and_identifier_boundaries_remain_untouched():
    alias = "732846000000000001"
    mapping = TokenMapping(original="42", surrogate=alias, token_type=TokenType.NUMBER)
    for text in (alias + "1", "a" + alias, alias + "_name"):
        assert MaskingEngine().unmask(text, "s", {alias: mapping}).unmasked_text == text
