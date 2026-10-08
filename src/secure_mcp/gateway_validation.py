"""Validate public request controls and envelopes before privacy transformation."""

import math
import re


def _enum(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"Unsupported {label}.")


def _public_options(value, options, label):
    if not isinstance(value, dict) or set(value) - options.keys():
        raise ValueError(f"Unsupported {label} controls.")
    for key, item in value.items():
        allowed = options[key]
        if allowed is None:
            if type(item) is not int or item < 1:
                raise ValueError(f"Invalid {label} token budget.")
        else:
            _enum(item, allowed, label)


def _content(value, dialect):
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        raise ValueError("Message content must be text or a list of content blocks.")
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Content blocks must be objects.")
        kind = block.get("type")
        if not isinstance(kind, str):
            raise ValueError("Content blocks require a public type.")
        if kind in {"text", "input_text", "output_text"}:
            if not isinstance(block.get("text"), str):
                raise ValueError("Text blocks require text.")
        elif kind == "refusal":
            if not isinstance(block.get("refusal"), str):
                raise ValueError("Refusal blocks require text.")
        elif kind == "tool_use":
            if not isinstance(block.get("input"), dict):
                raise ValueError("Tool input must be an object.")
        elif kind == "tool_result":
            if "content" in block:
                _content(block["content"], dialect)
        elif kind in {"thinking", "redacted_thinking", "reasoning"}:
            # The gateway separately checks provenance before forwarding opaque
            # provider-originated reasoning, including its signed contents.
            pass
        elif kind == "tool_reference":
            if not isinstance(block.get("tool_name"), str):
                raise ValueError("Tool references require a tool name.")
        else:
            raise ValueError("Unsupported message content block.")


def _tool_calls(value):
    if not isinstance(value, list):
        raise ValueError("Tool calls must be a list.")
    for call in value:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise ValueError("Invalid function call envelope.")
        function = call["function"]
        if not isinstance(function.get("name"), str) or not isinstance(
            function.get("arguments"), str
        ):
            raise ValueError("Function calls require a name and JSON arguments.")


def _message(message, dialect):
    roles = (
        {"user", "assistant"}
        if dialect == "claude"
        else {"system", "developer", "user", "assistant", "tool"}
    )
    _enum(message.get("role"), roles, "message role")
    if "tool_calls" in message:
        _tool_calls(message["tool_calls"])
    content = message.get("content")
    if (
        content is None
        and message["role"] == "assistant"
        and (message.get("tool_calls") or isinstance(message.get("refusal"), str))
    ):
        return
    _content(content, dialect)


def _input(value, dialect):
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        raise ValueError("Input must be text or a list of input items.")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Input items must be objects.")
        kind = item.get("type")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("Input items require a public type.")
        if "role" in item or kind == "message":
            _message(item, dialect)
        elif kind in {"function_call", "custom_tool_call"}:
            field = "arguments" if kind == "function_call" else "input"
            if not isinstance(item.get("name"), str) or not isinstance(
                item.get(field), str
            ):
                raise ValueError("Invalid tool call input item.")
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            output = item.get("output")
            if isinstance(output, list):
                _content(output, dialect)
            elif not isinstance(output, str):
                raise ValueError("Tool output must be text or content blocks.")
        elif kind == "reasoning":
            pass  # Provenance is checked by GatewaySession.walk.
        elif kind == "tool_namespace":
            if not isinstance(item.get("tools"), list):
                raise ValueError("Tool namespace requires a tools list.")
        else:
            raise ValueError("Unsupported input item.")


def validate_request(payload, dialect):
    """Reject malformed envelopes and private data disguised as public controls.

    Text/schema masking and opaque reasoning provenance remain the gateway's
    responsibility. This validates the controls that it copies without masking.
    """
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object.")
    if dialect not in {"claude", "codex", "responses", "chat_completions"}:
        raise ValueError("Unsupported request dialect.")
    if "model" in payload and (
        not isinstance(payload["model"], str)
        or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", payload["model"])
    ):
        raise ValueError("Invalid model ID.")
    for key in {
        "stream",
        "store",
        "parallel_tool_calls",
        "background",
    } & payload.keys():
        if not isinstance(payload[key], bool):
            raise ValueError("Request boolean controls must be booleans.")
    for key in {
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "top_k",
    } & payload.keys():
        if type(payload[key]) is not int or payload[key] < (0 if key == "top_k" else 1):
            raise ValueError("Request token controls must be integers in range.")
    for key in {"temperature", "top_p"} & payload.keys():
        value = payload[key]
        maximum = 1 if key == "top_p" or dialect == "claude" else 2
        if (
            type(value) not in {int, float}
            or not 0 <= value <= maximum
            or not math.isfinite(value)
        ):
            raise ValueError("Invalid sampling control.")
    for key, allowed in {
        "truncation": {"disabled"},
        "service_tier": {"auto", "default", "flex", "priority", "standard_only"},
    }.items():
        if key in payload:
            _enum(payload[key], allowed, key)
    if "reasoning" in payload:
        _public_options(
            payload["reasoning"],
            {
                "effort": {"none", "minimal", "low", "medium", "high", "xhigh"},
                "summary": {"auto", "concise", "detailed"},
            },
            "reasoning",
        )
    if "thinking" in payload:
        _public_options(
            payload["thinking"],
            {
                "type": {"enabled", "disabled", "adaptive"},
                "budget_tokens": None,
                "display": {"summarized", "omitted"},
            },
            "thinking",
        )
        if "type" not in payload["thinking"]:
            raise ValueError("Thinking controls require a public type.")
    if "include" in payload and (
        not isinstance(payload["include"], list)
        or any(item != "reasoning.encrypted_content" for item in payload["include"])
    ):
        raise ValueError("Unsupported include controls.")
    if "stop_sequences" in payload and (
        not isinstance(payload["stop_sequences"], list)
        or any(not isinstance(item, str) for item in payload["stop_sequences"])
    ):
        raise ValueError("Stop sequences must be a list of text.")
    if "output_config" in payload:
        output = payload["output_config"]
        if not isinstance(output, dict) or set(output) - {"effort", "format"}:
            raise ValueError("Unsupported output configuration.")
        if "effort" in output:
            _enum(output["effort"], {"low", "medium", "high", "max"}, "output effort")
        if "format" in output:
            format_value = output["format"]
            if (
                not isinstance(format_value, dict)
                or format_value.get("type") != "json_schema"
                or not isinstance(format_value.get("schema"), dict)
            ):
                raise ValueError("Output format must be a JSON Schema object.")
    for key in {
        "instructions",
        "prompt_cache_key",
        "safety_identifier",
    } & payload.keys():
        if not isinstance(payload[key], str):
            raise ValueError("Request text controls must be strings.")
    if "tools" in payload and (
        not isinstance(payload["tools"], list)
        or any(not isinstance(tool, dict) for tool in payload["tools"])
    ):
        raise ValueError("Tools must be a list of objects.")
    if dialect in {"claude", "chat_completions"}:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Messages must be a list.")
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("Messages must contain objects.")
            _message(message, dialect)
    else:
        _input(payload.get("input"), dialect)
