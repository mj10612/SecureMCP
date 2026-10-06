# Host compatibility and shared contract

This page separates a host's **configuration surface** (settings, login, hooks) from its
**wire protocol** (Anthropic Messages, OpenAI Responses, Chat Completions, other). A host is
only marked as supported for a protection path after that exact path was verified; MCP
connectivity alone is never treated as inference protection (`mask_text` tool arguments are
already visible to the provider).

## Compatibility table

Verification date: **2026-10-06**. "Verified" means an offline fixture or live test in this
repository exercises the stated path; it does not certify every host version.

| Host | Settings / auth | Wire protocol | Support level | Evidence / limits |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code 2.1.287 | `settings.json` `base_url` + local header; existing OAuth | Anthropic Messages / count_tokens | **Full inference gateway** (live verified) | `docs/GATEWAY.md`, `tests/test_gateway_live.py`; English/Korean file-read roundtrip |
| Codex CLI 0.160.0 | `config.toml` custom provider; existing ChatGPT login | OpenAI Responses / model catalog | **Full inference gateway** (live verified) | `docs/GATEWAY.md`, `tests/test_gateway_live.py`; English/Korean file-read roundtrip |
| Claude Code 2.1.287 | project/user `settings.json` command hooks | Claude Code hook JSON | **Partial hooks** (tool text only) | `docs/LOCAL_INTEGRATION.md`; prompts/attachments/telemetry are not covered |
| Codex CLI 0.160.0 | `.codex/hooks.json` / `$CODEX_HOME/hooks.json` | Codex hook JSON | **Partial hooks** (tool text only) | `docs/LOCAL_INTEGRATION.md`; no automatic screen restoration |
| Antigravity IDE / CLI | `mcpServers` stdio config (`agy mcp add/list/remove` verified against the real CLI in an isolated home); endpoint override **not verified** | Unknown (Gemini subscription path) | **MCP utility only** | `examples/antigravity_mcp_config.json`, `tests/test_antigravity_host.py`, `tests/test_examples.py`; do not treat as inference protection |
| Antigravity SDK | `LocalOpenAIAgentConfig(model=..., base_url=...)` to a local endpoint | OpenAI-compatible (assumed, **not verified**) | **Candidate** | Needs fixture verification before any claim |
| Grok Build / xAI API | `XAI_API_KEY` / `~/.grok/config.toml`; browser login exists | xAI `/v1/responses` (API); subscription reuse **not verified** | **Candidate** | Explicit API mode only, after verifying auth/cost; never reuse subscription tokens silently |
| Cursor, Cline/Roo Code, Continue, Aider, Gemini CLI | Not yet inspected | Mixed | **Investigation candidates** | Listed for tracking; no support claim |

Update this table only with a verification date, host/OS, support level and the test or
document that proves it. Keep unsupported cells explicit.

## Shared contract tests

Every adapter that claims a gateway path must pass the same offline contract. The module
`tests/test_host_contract.py` parametrizes protocol dialects (`claude`, `codex`) and checks:

| Contract item | Test |
| :--- | :--- |
| No private source/email/comment reaches the provider | `test_contract_private_source_never_reaches_provider` |
| Prompt and code share one alias; restoration is exact | `test_contract_prompt_and_code_share_alias` |
| Local tool arguments (file paths) are restored before execution | `test_contract_tool_arguments_are_restored` |
| Tool results are re-masked on the next request | `test_contract_tool_results_are_masked_on_followup` |
| Split SSE deltas are folded and restored | `test_contract_split_sse_is_restored` |
| Unknown/unbounded payloads fail closed | `test_contract_unsupported_payloads_fail_closed` |
| JSON Schema aliases and local `$ref` remain resolvable | `test_contract_schema_aliases_and_refs_resolve` |
| A local token is mandatory; API keys are refused | `test_contract_local_token_and_api_key_boundary` |
| Provider 401/403/429 are preserved with sanitized bodies | `test_contract_provider_statuses_are_preserved` |

Settings install/uninstall and idle-state expiry are covered by
`tests/test_gateway.py` (`test_gateway_install_preserves_settings_login_and_uninstalls`,
`test_uninstalled_config_can_be_reinstalled_with_a_new_port`) and the expiry tests
(`test_expired_gateway_session_is_not_revived_by_save`,
`test_expired_snapshot_is_discarded_without_losing_file`). A new host adapter is expected to
invoke the same module or copy its assertions, not to redefine weaker checks.

## Adding a new host

1. Identify the exact settings file, authentication owner and wire protocol from official
   documentation; record versions and the verification date.
2. Reuse `PrivacyGateway`, the masking engine and the settings backup pattern. Do not build
   a plugin framework before a second real adapter needs shared ownership; extend upstreams,
   routes and auth prefixes only.
3. Run the contract tests offline first. Live provider tests stay opt-in
   (`SECURE_MCP_LIVE_SUBSCRIPTION=1`) and must not run in normal CI.
4. Keep per-provider trust domains separate (state dir, port, token). Never forward one
   provider's credentials to another origin.
5. Update this table with the verified level, or leave the host in the candidate list.
