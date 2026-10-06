"""Offline validation for host MCP example configs (issue #65).

These tests only check the shipped JSON examples: valid JSON, a
secure-mcp stdio entry, no remote URLs and no credentials. They never launch
a host or contact a provider.
"""

import json
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CONFIGS = [
    "claude_desktop_config.json",
    "cursor_mcp_config.json",
    "antigravity_mcp_config.json",
]


def load(name):
    path = EXAMPLES / name
    assert path.is_file(), f"missing example: {name}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_example_configs_are_stdio_and_credential_free():
    for name in CONFIGS:
        config = load(name)
        servers = config.get("mcpServers")
        assert isinstance(servers, dict) and "secure-mcp" in servers
        entry = servers["secure-mcp"]
        assert isinstance(entry.get("command"), str) and entry["command"]
        assert isinstance(entry.get("args"), list) and "serve" in entry["args"]
        serialized = json.dumps(config)
        for marker in ["http://", "https://", "token", "api_key", "API_KEY"]:
            assert marker not in serialized, f"{name} leaks {marker!r}"


def test_antigravity_example_uses_local_stdio_serve():
    config = load("antigravity_mcp_config.json")
    entry = config["mcpServers"]["secure-mcp"]
    assert entry["args"] == ["-m", "secure_mcp", "serve"]
