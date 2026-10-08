"""Regression contracts for the October gateway issue review."""

import json

import pytest

from secure_mcp.gateway import GatewaySession


def test_schema_dependent_names_share_property_aliases():
    session = GatewaySession()
    schema = {
        "type": "object",
        "properties": {
            "privateField": {"type": "string"},
            "otherField": {"type": "string"},
        },
        "dependentRequired": {"privateField": ["otherField"]},
        "dependentSchemas": {"privateField": {"required": ["otherField"]}},
    }
    masked = session.walk(schema)
    assert "privateField" not in json.dumps(masked)
    assert "otherField" not in json.dumps(masked)
    first, second = masked["properties"]
    assert masked["dependentRequired"] == {first: [second]}
    assert masked["dependentSchemas"] == {first: {"required": [second]}}


def test_regex_schema_constraints_fail_closed():
    with pytest.raises(ValueError, match="pattern"):
        GatewaySession().request(
            {
                "input": [],
                "tools": [
                    {
                        "type": "function",
                        "name": "privateTool",
                        "parameters": {
                            "type": "object",
                            "patternProperties": {
                                "^privatePattern$": {"type": "string"}
                            },
                        },
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "field", ["reasoning", "thinking", "temperature", "max_tokens"]
)
@pytest.mark.parametrize("envelope", ["messages", "input"])
def test_subscription_controls_reject_hidden_private_values(field, envelope):
    with pytest.raises(ValueError):
        GatewaySession().request(
            {envelope: [], field: {"private": "alice@example.com"}}
        )


@pytest.mark.parametrize(
    "messages", [{}, None, "private", [None], [{"role": "user", "content": {}}]]
)
def test_chat_messages_structure_is_validated(messages):
    with pytest.raises(ValueError):
        GatewaySession().request(
            {"model": "fixture", "messages": messages}, dialect="chat_completions"
        )


@pytest.mark.parametrize("dialect", ["claude", "responses", "chat_completions"])
@pytest.mark.parametrize("fragment", ["", "   "])
def test_empty_streamed_tool_arguments_are_supported(dialect, fragment):
    if dialect == "claude":
        event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": fragment},
        }
    elif dialect == "responses":
        event = {
            "type": "response.function_call_arguments.delta",
            "item_id": "call1",
            "delta": fragment,
        }
    else:
        event = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": fragment},
                            }
                        ]
                    },
                }
            ]
        }
    body = ("data: " + json.dumps(event) + "\n\n").encode()
    restored = GatewaySession().response(body, "text/event-stream", dialect=dialect)
    assert json.loads(restored.decode().split("data: ")[1]) == event
