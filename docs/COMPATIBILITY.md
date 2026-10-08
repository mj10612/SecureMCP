# Host compatibility and shared contract

This page separates a host's **configuration surface** (settings, login, hooks) from its
**wire protocol** (Anthropic Messages, OpenAI Responses, Chat Completions, other). A host is
only marked as supported for a protection path after that exact path was verified; MCP
connectivity alone is never treated as inference protection (`mask_text` tool arguments are
already visible to the provider).

## Compatibility table

Verification date: **2026-10-08**. "Verified" means an offline fixture or live test in this
repository exercises the stated path; it does not certify every host version.

| Host | Settings / auth | Wire protocol | Support level | Evidence / limits |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code 2.1.287 | `settings.json` `base_url` + local header; existing OAuth | Anthropic Messages / count_tokens | **Full inference gateway** (live verified) | `docs/GATEWAY.md`, `tests/test_gateway_live.py`; English/Korean file-read roundtrip |
| Codex CLI 0.160.0 | `config.toml` custom provider; existing ChatGPT login | OpenAI Responses / model catalog | **Full inference gateway** (live verified) | `docs/GATEWAY.md`, `tests/test_gateway_live.py`; English/Korean file-read roundtrip |
| Claude Code 2.1.287 | project/user `settings.json` command hooks | Claude Code hook JSON | **Partial hooks** (tool text only) | `docs/LOCAL_INTEGRATION.md`; prompts/attachments/telemetry are not covered |
| Codex CLI 0.160.0 | `.codex/hooks.json` / `$CODEX_HOME/hooks.json` | Codex hook JSON | **Partial hooks** (tool text only) | `docs/LOCAL_INTEGRATION.md`; no automatic screen restoration |
| Antigravity CLI 1.3.0 / IDE version unverified | `mcpServers` stdio config (`agy mcp add/list/remove` verified in an isolated home); subscription endpoint override **not verified** | MCP stdio; Gemini inference path unknown | **MCP utility only** | Windows 11 build 26200 / Python 3.11; existing servers, conflicts, spaces and real initialize/list/tools call tested; [guide](ANTIGRAVITY.md) |
| Antigravity SDK 0.1.21 | `LocalOpenAIAgentConfig` + context-owned capability-authenticated bridge; mandatory local token and explicit xAI API key | Native Chat Completions SSE | **Offline API gateway verified** | Windows 11 build 26200 / Python 3.11; actual English/Korean native `view_file` roundtrip, provider payload masking, split tool/text SSE restoration, auth/bounds/unknown/compaction rejection; `tests/test_antigravity_host.py`, [guide](ANTIGRAVITY.md); no Gemini subscription redirect |
| xAI API (no live model version claimed) | Explicit `gateway api-install/run/uninstall`; caller-owned `XAI_API_KEY`, isolated local token/state | Responses / Chat Completions, JSON and SSE | **Offline API gateway verified** | Fixed `https://api.x.ai`; function tools only; `tests/test_xai_api.py`, `tests/test_xai_install.py`; [guide](GROK.md) |
| Grok Build 1.0.46 | Custom model, explicit env_key and local header; default/browser login unchanged | Responses SSE | **Offline API gateway verified** | Windows / Python 3.11, 2026-10-07; English/Korean native read_file, split tool/text SSE, masked followup and local restoration; [guide](GROK.md), `tests/test_grok_host.py`; no subscription reuse claim |
| Cursor, Cline/Roo Code, Continue, Aider, Gemini CLI | Not yet inspected | Mixed | **Investigation candidates** | Listed for tracking; no support claim |

Update this table only with a verification date, host/OS, support level and the test or
document that proves it. Keep unsupported cells explicit.

Claude/Codex native gateway evidence is on Windows (versions above, 2026-10-06);
Python/OS portability is separately checked by CI. Candidate versions/OS are unknown
unless explicitly listed. CI portability alone does not prove native-host compatibility.

## Protection and authentication surfaces

| Host/path | Endpoint override | Local stdio MCP | Before-send protection | Tool argument / display restoration | Remote/cloud scope and cost |
| --- | --- | --- | --- | --- | --- |
| Claude/Codex gateway | Verified | Separate utility | Whole supported gateway request; legacy hooks cover tool text only | Both verified through gateway | Local text/code inference; subscription quota; no API fallback |
| Antigravity CLI/IDE | Subscription override unknown | CLI verified; IDE documented format only | Not verified | MCP does not intercept agent execution or screen | Gemini login stays host-owned; live tests consume plan quota |
| Antigravity SDK | Verified Chat Completions endpoint through authenticated short-lived bridge | Separate from SDK endpoint | Native request and file-result masking verified offline | Native `view_file` arguments and answer restored; split SSE verified | Explicit xAI API billing; Gemini login unchanged; SDK telemetry/local history outside boundary |
| xAI API gateway | Fixed API origin behind local endpoint | Independent | Supported requests masked before forwarding | Offline function arguments and split SSE verified | API billing; server search/code execution/remote MCP/files blocked |
| Grok custom model | Verified base_url and extra_headers | Not investigated here | Native offline request masking verified | Native read_file arguments and answer restored | Browser tokens never reused; external tools/telemetry/cloud outside boundary |
| Cursor, Cline/Roo Code, Continue, Aider, Gemini CLI | Unknown | Unknown | Unknown | Unknown | No verified version, auth, cost or protection claim |

Versions/dates/evidence are recorded above; unknown cells need product-specific investigation.
API mode uses a separate port, token and state directory. Credentials remain provider-owned.

## Shared contract tests

Every adapter that claims a gateway path must pass the same offline contract. The module
`tests/test_host_contract.py` parametrizes protocol dialects (`claude`, `codex`, `xai`) and checks:

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
| 401 then refreshed bearer keeps aliases/state | `test_contract_refresh_preserves_aliases_and_provider_domains` |
| Provider auth, encrypted state and reasoning provenance are isolated | `test_contract_refresh_preserves_aliases_and_provider_domains` |

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
   xAI uses a separate `SECURE_MCP_LIVE_XAI_API=1` flag and caller-owned API key/model;
   those tests incur API charges, independently of subscription limits.
4. Keep per-provider trust domains separate (state dir, port, token). Never forward one
   provider's credentials to another origin.
5. Update this table with the verified level, or leave the host in the candidate list.


The explicit xAI gateway supports `GET /xai/v1/models`. Both the local
`X-SecureMCP-Token` and caller-owned `Authorization: Bearer xai-...` are required.
The gateway fetches only the fixed provider catalog, rejects redirects/environment proxies,
and bounds JSON size. It returns public model IDs and bounded public metadata; arbitrary
provider descriptions are omitted. This enables catalog discovery for compatibility probes;
it does not certify Cursor/Cline or other untested hosts, and no generic `/v1/models`
subscription route or credential fallback is enabled.
