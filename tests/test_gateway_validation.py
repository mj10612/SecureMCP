"""Typed public controls and message-envelope validation contracts."""

import pytest

from secure_mcp.gateway_validation import validate_request


@pytest.mark.parametrize(
    "dialect", ["claude", "codex", "responses", "chat_completions"]
)
@pytest.mark.parametrize(
    "options",
    [
        {"reasoning": {"private": "alice@example.com"}},
        {"reasoning": {"effort": "alice@example.com"}},
        {"reasoning": {"summary": {"private": "alice@example.com"}}},
        {"thinking": {"type": "enabled", "private": "alice@example.com"}},
        {"thinking": {"type": "alice@example.com"}},
        {"thinking": {"type": "enabled", "budget_tokens": "alice@example.com"}},
        {"thinking": {"type": "enabled", "budget_tokens": True}},
        {"stream": "private"},
        {"store": 1},
        {"parallel_tool_calls": {}},
        {"max_tokens": {"private": "alice@example.com"}},
        {"max_tokens": True},
        {"top_k": -1},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"temperature": 10**400},
        {"top_p": 1.1},
        {"include": ["alice@example.com"]},
        {"service_tier": {"private": "secret"}},
        {"model": {"private": "alice@example.com"}},
    ],
)
def test_private_data_and_malformed_public_controls_are_rejected(dialect, options):
    field = "messages" if dialect in {"claude", "chat_completions"} else "input"
    with pytest.raises(ValueError):
        validate_request({field: [], **options}, dialect)


@pytest.mark.parametrize("dialect", ["claude", "chat_completions"])
@pytest.mark.parametrize(
    "messages",
    [
        {},
        None,
        "private",
        [None],
        ["private"],
        [{"role": "user", "content": {}}],
        [{"role": "user", "content": None}],
        [{"role": "private", "content": "text"}],
        [{"role": "user", "content": [None]}],
        [{"role": "user", "content": [{"type": "text", "text": {}}]}],
    ],
)
def test_malformed_messages_fail_as_validation_errors(dialect, messages):
    with pytest.raises(ValueError):
        validate_request({"messages": messages}, dialect)


@pytest.mark.parametrize(
    "input_value",
    [
        {},
        None,
        1,
        [None],
        ["private"],
        [{"role": "user", "content": {}}],
        [{"type": "function_call", "name": "tool", "arguments": {}}],
    ],
)
def test_malformed_responses_input_fails_as_validation_error(input_value):
    with pytest.raises(ValueError):
        validate_request({"input": input_value}, "codex")


def test_native_claude_controls_and_tool_history_are_supported():
    validate_request(
        {
            "model": "claude-fixture",
            "stream": True,
            "max_tokens": 2048,
            "temperature": 0.5,
            "top_p": 0.9,
            "top_k": 0,
            "service_tier": "standard_only",
            "thinking": {
                "type": "enabled",
                "budget_tokens": 1024,
                "display": "summarized",
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "private.py"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "private source"}],
                        }
                    ],
                },
            ],
        },
        "claude",
    )


def test_native_codex_controls_and_replay_envelopes_are_supported():
    validate_request(
        {
            "model": "gpt-fixture",
            "stream": True,
            "store": False,
            "reasoning": {"effort": "xhigh", "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": True,
            "input": [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "private instructions"}],
                },
                {"type": "reasoning", "encrypted_content": "opaque"},
                {
                    "type": "function_call",
                    "name": "read_file",
                    "arguments": '{"path":"private.py"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "t1",
                    "output": "private source",
                },
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "private command",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": "t2",
                    "output": "private output",
                },
                {
                    "type": "tool_namespace",
                    "name": "functions",
                    "tools": [{"type": "custom", "name": "exec"}],
                },
            ],
        },
        "codex",
    )


def test_chat_assistant_tool_call_may_have_null_content():
    validate_request(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                }
            ]
        },
        "chat_completions",
    )


def test_responses_text_input_is_supported():
    validate_request({"input": "private source"}, "responses")


def test_provider_refusal_history_is_supported():
    validate_request(
        {
            "input": [
                {
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "private refusal"}],
                }
            ]
        },
        "codex",
    )
    validate_request(
        {
            "messages": [
                {"role": "assistant", "content": None, "refusal": "private refusal"}
            ]
        },
        "chat_completions",
    )


@pytest.mark.parametrize(
    "options",
    [
        {"stop_sequences": {"id": "alice@example.com"}},
        {"stop_sequences": [{"type": "private"}]},
        {"output_config": {"effort": {"private": "alice@example.com"}}},
        {"output_config": {"effort": "alice@example.com"}},
    ],
)
def test_subscription_walk_bypass_positions_require_typed_controls(options):
    with pytest.raises(ValueError):
        validate_request({"messages": [], **options}, "claude")


def test_native_adaptive_thinking_and_structured_output_are_supported():
    validate_request(
        {
            "messages": [],
            "thinking": {"type": "adaptive"},
            "stop_sequences": [],
            "output_config": {
                "effort": "max",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"private": {"type": "string"}},
                    },
                },
            },
        },
        "claude",
    )


@pytest.mark.parametrize(
    "value",
    [
        {"messages": [{"role": "user", "content": [{"type": {}}]}]},
        {"input": [{"type": {}}]},
    ],
)
def test_unhashable_block_types_raise_validation_error(value):
    dialect = "claude" if "messages" in value else "codex"
    with pytest.raises(ValueError):
        validate_request(value, dialect)
