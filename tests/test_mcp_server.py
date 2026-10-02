"""Tests for MCP Server tools, prompts, and resources."""

import json
import pytest
from secure_mcp.server import app, unmask_text, unmask_code


@pytest.mark.asyncio
async def test_mcp_list_tools():
    tools = await app.list_tools()
    tool_names = [t.name for t in tools]
    assert "mask_text" in tool_names
    assert "unmask_text" not in tool_names
    assert "mask_code" in tool_names
    assert "unmask_code" not in tool_names
    assert "create_privacy_session" in tool_names
    assert "get_session_stats" in tool_names
    assert "clear_privacy_session" in tool_names


@pytest.mark.asyncio
async def test_mcp_mask_and_unmask_tools():
    # 1. Mask text
    mask_res_raw = await app.call_tool(
        "mask_text",
        {
            "text": "Alice from Acme Corp sent 500 Bitcoins to Bob.",
            "session_id": "mcp_test_sess",
            "strategy": "bracket",
        },
    )

    # In FastMCP/MCPServer, result content is in content[0].text
    mask_json = json.loads(mask_res_raw.content[0].text)
    assert mask_json["masked_tokens"] > 0
    assert "Acme" not in mask_json["masked_text"]
    assert "Alice" not in mask_json["masked_text"]

    # 2. Unmask text
    unmask_json = json.loads(
        unmask_text(mask_json["masked_text"], session_id="mcp_test_sess")
    )
    assert (
        unmask_json["unmasked_text"] == "Alice from Acme Corp sent 500 Bitcoins to Bob."
    )


@pytest.mark.asyncio
async def test_mcp_code_tools():
    code = "def get_secret():\n    return 'sensitive_token_123'"
    mask_res_raw = await app.call_tool(
        "mask_code",
        {
            "code": code,
            "session_id": "mcp_code_sess",
        },
    )
    mask_json = json.loads(mask_res_raw.content[0].text)
    assert "get_secret" not in mask_json["masked_text"]
    assert "sensitive_token_123" not in mask_json["masked_text"]

    unmask_json = json.loads(
        unmask_code(mask_json["masked_text"], session_id="mcp_code_sess")
    )
    assert unmask_json["unmasked_code"] == code


@pytest.mark.asyncio
async def test_mcp_prompts():
    prompts = await app.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "privacy_preserving_assistant" in prompt_names
    assert "secure_code_assistant" in prompt_names

    p_data = await app.get_prompt(
        "privacy_preserving_assistant", {"task_description": "Summarize legal brief"}
    )
    assert len(p_data.messages) > 0
    assert "SecureMCP" in p_data.messages[0].content.text


@pytest.mark.asyncio
async def test_mcp_resources():
    resources = await app.list_resources()
    resource_uris = [str(r.uri) for r in resources]
    assert "privacy://policies" in resource_uris
    assert "privacy://status" in resource_uris

    # Read policy resource
    policies = await app.read_resource("privacy://policies")
    assert "content_words" in str(policies)
