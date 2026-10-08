import pytest

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.session import PrivacySession


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_directives_are_contextual_and_headers_and_macros_remain_private(language):
    source = (
        '#include <stdio.h>\n#include "private/customer/header.h"\n'
        '#define PRIVATE_LIMIT 42\n#ifdef PRIVATE_LIMIT\n'
        '#if defined(PRIVATE_LIMIT)\n#elif PRIVATE_LIMIT > 0\n#else\n#endif\n#endif\n'
        '#undef PRIVATE_LIMIT\n#pragma once\n#line 123 "private/source.cpp"\n'
        'int include, define, once, defined;\n'
    )
    session = PrivacySession("preprocessor")
    engine = MaskingEngine()
    masked = engine.mask_code(
        source, session.session_id, session.generator,
        session.forward_store, session.reverse_store, language=language,
    ).masked_text
    for directive in ("include", "define", "ifdef", "if", "elif", "else", "endif", "undef", "pragma", "line"):
        assert f"#{directive}" in masked
    assert "#pragma once" in masked
    assert "#if defined(" in masked
    assert "#include <smcp_LIT_" in masked
    assert '#include "smcp_LIT_' in masked
    for private in ("PRIVATE_LIMIT", "private/", "stdio.h", "int include"):
        assert private not in masked
    for identifier in ("include", "define", "once", "defined"):
        assert f"code:identifier:{identifier}" in session.forward_store
    assert engine.unmask(masked, session.session_id, session.reverse_store, strict=True).unmasked_text == source


def test_directive_spacing_leading_comments_and_continuations():
    source = (
        '/* prefix */ # include <private/header.h>\n'
        '# define PRIVATE_MACRO(value) \\\n    ((value) + 42)\n'
        '#pragma once\nint privateValue = PRIVATE_MACRO(1);'
    )
    session = PrivacySession("preprocessor")
    engine = MaskingEngine()
    masked = engine.mask_code(source, "preprocessor", session.generator,
                              session.forward_store, session.reverse_store, language="cpp").masked_text
    assert "# include <" in masked
    assert "# define " in masked
    assert "\\\n" in masked
    assert engine.unmask(masked, "preprocessor", session.reverse_store, strict=True).unmasked_text == source


@pytest.mark.parametrize("directive", ["error", "warning"])
def test_diagnostic_payload_is_opaque_even_when_words_are_keywords(directive):
    session = PrivacySession("preprocessor")
    engine = MaskingEngine()
    source = f"#{directive} return class private/customer\n"
    masked = engine.mask_code(source, "preprocessor", session.generator,
                              session.forward_store, session.reverse_store, language="cpp").masked_text
    assert f"#{directive} smcp_LIT_" in masked
    assert "return class" not in masked
    assert engine.unmask(masked, "preprocessor", session.reverse_store, strict=True).unmasked_text == source


def test_unterminated_include_header_fails_closed():
    session = PrivacySession("preprocessor")
    with pytest.raises(ValueError, match="Unterminated"):
        MaskingEngine().mask_code('#include <private/header.h\nint value;', "preprocessor",
                                 session.generator, session.forward_store,
                                 session.reverse_store, language="cpp")
