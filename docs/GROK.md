# Grok Build with the explicit xAI API gateway

This integration uses a user-supplied xAI API key. API billing and rate limits
apply; it does not route a Grok subscription through the gateway.

Grok Build supports custom models in its user configuration
(`$GROK_HOME/config.toml`, or `~/.grok/config.toml`). Its
[official settings reference](https://docs.x.ai/build/settings/reference)
documents `api_backend = "responses"`, `env_key`, `extra_headers`, and
`supports_backend_search`. Project `.grok/config.toml` does not contribute model
configuration.

Install the gateway configuration with an explicit upstream model:

```sh
secure-mcp gateway api-install --provider xai --model YOUR_XAI_MODEL
secure-mcp gateway api-run
```

Set `XAI_API_KEY` in the environment where Grok Build runs, then select the
`secure_mcp_xai` custom model. The key is read from the environment; do not put it
in a prompt or configuration file. The generated model uses the loopback gateway
at `http://127.0.0.1:38118/xai/v1`, the Responses protocol, and a separate local
gateway token. Backend search is disabled for this custom model.

The provider receives masked request text. The gateway restores mapped text in
model responses before Grok receives it, including streamed text and tool
arguments. Grok executes tools locally; subsequent tool output passes through
the same masking boundary. The gateway does not validate or authorize Grok's
local tool execution, so retain the host's permission controls.

API configuration is stored separately in `~/.secure-mcp/xai-api/config.json`;
the API key is never written there. API mode runs in the foreground and has no
login startup registration. Installation preserves the default model, other
models, MCP/hooks settings and login files. Uninstall restores the original
settings bytes and refuses to overwrite later user edits; merge those edits
with the backup in `installation.json` before uninstalling. With custom paths,
pass the same `--config` to install, run and uninstall; `--grok-home` selects the
Grok configuration directory at install time.

Only local function tools are accepted. Provider-side search, code execution,
remote MCP, remote files and unsupported payloads are rejected. Local external
tools, telemetry and cloud agents remain outside this inference protection.
Requests cannot fall back to direct traffic or browser subscription tokens.

To remove the generated custom model:

```sh
secure-mcp gateway api-uninstall
```

## Offline native-host verification

The optional native test uses an isolated home, a synthetic API key, and a
local mock Responses provider. External traffic is sent to the local gateway,
which rejects unsupported destinations. It does not call paid inference or
read an existing Grok login. Supply a separately installed official Grok binary:

```powershell
$env:SECURE_MCP_GROK_BINARY = 'C:\path\to\grok.exe'
.venv\Scripts\python.exe -m pytest tests/test_grok_host.py -q
```

Without that environment variable, the native test reports a skip. A passing
mock test verifies the installed client's protocol compatibility; it does not
verify account entitlements or live xAI availability.

Grok Build 1.0.46 on Windows was verified against the local fixture with English
and Korean prompts, an actual `read_file(target_file)` execution, split streamed
tool arguments and answer text, masked tool output, and restored answers. The
fixture file remained unchanged. Live paid xAI inference was not exercised.

The real API smoke test defaults to skip. To run it intentionally, set
`SECURE_MCP_LIVE_XAI_API=1`, `SECURE_MCP_XAI_MODEL` and `XAI_API_KEY` in the test
process, then run `pytest tests/test_xai_api.py -k live_xai_api_smoke`.
It consumes separately billed API usage, not a Grok subscription allowance.
