import pytest

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.models import MaskMode
from secure_mcp.session import PrivacySession


@pytest.mark.parametrize("term,suffix", [
    ("Google DeepMind", "와의"), ("홍길동 연구원", "에게"),
    ("홍길동", "은"), ("Private Organization", "에서부터"),
    ("홍길동 연구원", "들에게"), ("Google DeepMind", "입니다"),
])
def test_explicit_term_is_atomic_before_complete_korean_suffix(term, suffix):
    engine = MaskingEngine()
    session = PrivacySession("particles")
    source = term + suffix + " 협업."
    result = engine.mask_text(source, session.session_id, session.generator,
                              session.forward_store, session.reverse_store,
                              mode=MaskMode.ENTITIES_ONLY, sensitive_terms={term})
    mapping = session.forward_store["text:entity:" + term]
    assert result.masked_text.startswith(mapping.surrogate + suffix)
    assert engine.unmask(result.masked_text, session.session_id,
                         session.reverse_store, strict=True).unmasked_text == source


def test_longest_term_wins_and_each_occurrence_reuses_one_mapping():
    engine = MaskingEngine()
    session = PrivacySession("particles")
    terms = {"Google", "Google DeepMind", "Google DeepMind 연구원"}
    source = "Google DeepMind 연구원에게 Google DeepMind 연구원과의 협업"
    result = engine.mask_text(source, "particles", session.generator,
                              session.forward_store, session.reverse_store,
                              sensitive_terms=terms)
    mapping = session.forward_store["text:entity:Google DeepMind 연구원"]
    assert mapping.occurrence_count == 2
    assert "text:entity:Google" not in session.forward_store
    assert engine.unmask(result.masked_text, "particles", session.reverse_store).unmasked_text == source


@pytest.mark.parametrize("source", [
    "Google DeepMinder", "Google DeepMind와의미", "앞Google DeepMind와의",
    "홍길동 연구원연구", "홍길동 연구원에게서는비밀",
])
def test_explicit_entity_does_not_match_prefix_or_incomplete_particle(source):
    terms = {"Google DeepMind", "홍길동 연구원"}
    chunks = MaskingEngine()._chunks(source, terms)
    assert not any(chunk.kind == "ENTITY" and chunk.text in terms for chunk in chunks)
