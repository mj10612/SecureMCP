"""Prose and unfenced source must retain their respective masking policies."""

import pytest

from secure_mcp.gateway_source import looks_like_source


@pytest.mark.parametrize(
    "text",
    [
        "Please review the implementation.\nnote = see below\nCheck the tests too.",
        "note = see below",
        "The note = see below is part of the explanation.",
        "Please review this example:\nprivateValue = 42\nExplain the result.",
        "Compare the class methods before editing.",
        "Let me explain the issue.",
        "Select the best option.",
        "Hello",
        "42",
        "",
        "# Just a comment",
        "// Just a comment",
        "- review\n- tests",
        "Review - tests",
    ],
)
def test_prose_does_not_become_source_from_embedded_assignment(text):
    assert not looks_like_source(text)


@pytest.mark.parametrize(
    "text",
    [
        "privateValue = 42",
        'privateValue = "private literal"',
        "privateValue = otherValue + 1",
        "def privateFunction(privateValue):\n    return privateValue + 42",
        "class PrivateClass:\n    pass",
        "from privateModule import privateFunction",
        "import privateModule\nprivateFunction(privateValue)",
        "# Source comment\nprivateValue = 42",
        "privateFunction(privateValue)",
        "const privateValue = 42;",
        "let privateValue: string = 'secret';",
        "export function privateFunction(privateValue) { return privateValue; }",
        "interface PrivateType { privateValue: string; }",
        "type PrivateType = string;",
        'import { privateFunction } from "private-module";',
        "// Source comment\nconst privateValue = 42;",
        "/* Source comment */\nconst privateValue = 42;",
        "package main\nfunc privateFunction() { return }",
        "func privateFunction() { return }",
        "fn private_function() { let private_value = 42; }",
        "pub async fn private_function() { }",
        "use private_module::private_function;",
        "mod private_module;",
        "SELECT private_column FROM private_table;",
        "-- SQL comment\nSELECT private_column FROM private_table;",
        "SELECT 42;",
        "SELECT private_value;",
        "INSERT INTO private_table VALUES (42);",
        "UPDATE private_table SET private_column = 42;",
        "DELETE FROM private_table;",
        "CREATE TABLE private_table (private_column INT);",
        '#include "private-header.h"\nint privateFunction() { return 42; }',
        '#include "private-header.h"',
    ],
)
def test_complete_and_explicit_source_context_is_recognized(text):
    assert looks_like_source(text)
