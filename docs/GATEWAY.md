# Automatic subscription integration

SecureMCP 0.5.1 provides an experimental authenticated loopback gateway for Claude Code and
Codex. Set it up once, then use their normal commands. Requests are masked automatically;
replies and tool arguments are restored before the CLI displays or executes them. The next
request masks restored history again. No agent hooks are registered.

## Setup and existing logins

Keep your existing Claude/ChatGPT subscription login:

```bash
uv tool install .
secure-mcp gateway install
secure-mcp gateway status
```

Restart both agents. No per-prompt wrapper, file list or manual restoration is required.
If an earlier gateway is already running, update/restart it from the repository:

```bash
uv tool install --force .
secure-mcp gateway stop
secure-mcp gateway install
```

Then restart both agents. These steps are needed only when updating the installed gateway.
`--agent claude` / `--agent codex` limits setup to one host; default is `both`.
`--port` selects the initial port, and `--config` selects local state. Installation is user-wide.

| Agent | Settings changed | Authentication |
| --- | --- | --- |
| Claude Code | `$CLAUDE_CONFIG_DIR/settings.json`, default `~/.claude/settings.json`: base URL and local custom header | Existing OAuth and `anthropic-beta`; no API key/helper added |
| Codex | `$CODEX_HOME/config.toml`, default `~/.codex/config.toml`: provider with `requires_openai_auth = true`, HTTP transport, ChatGPT login selection | Existing CLI-owned ChatGPT OAuth and account header |

These settings follow [Claude's subscription gateway documentation](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)
and [Codex's proxy authentication option](https://learn.chatgpt.com/docs/auth#alternative-model-providers).
OAuth is forwarded to Anthropic's service / the ChatGPT Codex backend. API-key requests
are refused; there is no automatic switch to separately billed API usage. Existing plan
limits and provider acceptance still apply. Login caches/keychains and token refresh remain
CLI-owned; SecureMCP never reads or copies them. The local access header is stripped upstream.
Provider 401/403/429 statuses are retained with sanitized error bodies.

Unrelated hooks, permissions and model choices are preserved. Remove old project privacy
hooks before using this gateway: combining independent mapping tables can change references.
The installer removes old SecureMCP hooks in the Claude user settings it edits, does not
scan other repositories and does not register new hooks.

## Automatic startup and removal

Setup starts a detached process immediately and registers user-login startup:

| Platform | Registration |
| --- | --- |
| Windows | `Startup/SecureMCP.vbs`, hidden WScript launch |
| macOS | `~/Library/LaunchAgents/io.securemcp.gateway.plist`, `RunAtLoad` |
| Linux desktop | `$XDG_CONFIG_HOME/autostart/secure-mcp.desktop`, default `~/.config/autostart/`, `Terminal=false` |

No administrator access or agent lifecycle hook is needed. Linux autostart requires a
desktop login; headless hosts can run the foreground command under their own supervisor.

```bash
secure-mcp gateway status
secure-mcp gateway stop
secure-mcp gateway run       # foreground restart/diagnostics; Ctrl-C stops it
secure-mcp gateway uninstall
```

If the daemon stops, configured inference fails at the loopback endpoint. There is no raw
direct-provider fallback. `uninstall` stops it and restores exact backed-up settings/startup
bytes. It refuses to overwrite files edited since setup; manually merge originals recorded
in private `installation.json` in that case. State/backups remain in `~/.secure-mcp/gateway`
by default for recovery; remove this directory after stopping if you want to discard them.
Do not commit local state or reuse its access token for other services.

## Shared aliases and automatic results

A prompt referring to `A` and code containing `def A(...)` use the same identifier alias.
Function/variable names, literal values, numbers and comments are masked. Filename aliases
are restored for native local tools; their results are masked in the next inference request.
English, Korean and mixed references reuse the multilingual engine.

Fenced code uses its language label; inline code is lexed and tool output uses language
detection. A fragment that cannot be lexed safely (for example a backtick span containing an
apostrophe) falls back to text masking instead of failing the request. Ordinary text uses
`content_words`, known terms and uppercase single-letter references. This remains heuristic:
ambiguous/unfenced or unsupported code needs clearer context. Grammar, syntax, public
built-in tool names and protocol IDs stay visible. Masking does not hide algorithms or
inferred meaning; it can reduce reasoning/edit quality.

Common review/task vocabulary is retained in ordinary instructions to keep requests usable;
known source identifiers take priority over that vocabulary. Code lexing has no identifier
preservation exception. Core tool descriptions use fixed public descriptions, and an explicit
privacy instruction tells the model to keep supplied aliases unchanged.

Gateway identifiers use `private_symbol_<number>`. A controlled live comparison found that
the previous `smcp_ID_` aliases caused Sonnet 5.5 to refuse benign masked requests, while
neutral aliases succeeded with the same masking. This does not establish the classifier's
internal cause. No safety filter is disabled and no raw-source fallback is used. Other
SecureMCP integrations retain their existing alias format.

Claude's first system attribution block (`x-anthropic-billing-header`) is protocol,
not source text. Its observed version, entry-point and optional hexadecimal fingerprint
fields are validated and preserved exactly in the first position, together with validated
cache-control tags. Unknown fields, extra text, moved/merged blocks and invalid cache tags
fail closed. Exact public Claude CLI/Agent SDK identity sentences also remain visible;
those sentences inside source literals are still masked. All other system context is
masked, and the privacy instruction is appended after the original blocks. Anthropic
[documents this positional attribution contract](https://code.claude.com/docs/en/llm-gateway-protocol#system-prompt-attribution-block).

JSON replies and SSE text/tool-argument deltas are restored. Streams are buffered until
complete so split aliases can be folded before restoration. This adds latency and removes
live token-by-token display. Unknown/altered aliases fail rather than inventing originals.
Schema properties and tool-argument JSON keys share aliases. Local `$ref` pointers keep
keywords and array indexes so masked schemas still resolve. Only pinned public Codex
`exec` / `apply_patch` grammars pass unchanged; other custom grammars are blocked. Claude's
safety classifier keeps public policy tags while private paths/rule operands are masked.
Recognized public JSON Schema dialect URIs in `$schema` stay unchanged; other
`$schema` values are masked like ordinary text. Schema descriptions and private properties
remain masked, including URI literals in source. `enum`/`const`/`default`/`examples` values
are treated as data: protocol-named keys such as `id`, `type` or `cache_control` inside
them are still masked. Operational schema fields and JSON scalar numeric parameters retain
their types; numbers in source text are masked.

Signed Claude thinking (including `redacted_thinking`), and encrypted Codex reasoning remain
exactly masked and are not restored for display. Only blocks previously emitted by this
gateway can be replayed. Codex may omit an empty reasoning `content` list on replay; that
empty field is normalized for provenance checking. Ciphertext, summaries and nonempty
content remain validated.
Claude/Codex have separate tables, shared across this installation's conversations/accounts;
requests per agent are serialized. Use separate configurations/ports for separate trust domains.

Encrypted snapshots reuse SecureMCP's authenticated encryption and password KDF. They preserve
aliases across daemon restart. Reasoning provenance stores only hashes. Snapshots expire after
24 hours idle; the running daemon and a restarted daemon both drop expired aliases and
provenance (files are not automatically deleted). Expired/corrupt reasoning state blocks
replay; start a fresh conversation/reset state rather than bypass masking. POSIX private file
modes are applied; Windows uses inherited user ACLs. The key/local token resides alongside
snapshots, so this does not protect against another process with access to that user's files.

Version 0.5.1 uses `<agent>-symbols-v2.enc` with a matching hash-only `.opaque.json` file.
Old `<agent>.enc` snapshots remain untouched for rollback; the snapshot reader still supports
legacy aliases. After upgrading from 0.5.0, start new Claude/Codex conversations once so
previous reasoning does not replay against the new alias table. Do not delete old state
until you no longer need rollback.

## Protection boundary and tests

Supported routes: Anthropic Messages/token counting, Codex Responses/model catalog.
Only IPv4 loopback can bind; a local access token is mandatory. Production upstreams are
fixed HTTPS origins; redirects and environment proxies are disabled when forwarding OAuth.
Requests/replies are bounded to 16 MiB; upstream response reads time out after 180 seconds.

Explicit xAI API-key mode is **opt-in through separate API commands**:
`/xai/v1/responses` and `/xai/v1/chat/completions` forward the caller's own API key to the
fixed origin, and subscription tokens are refused there. The subscription installer never
enables API mode, and there is no automatic fallback in either direction. xAI subscription
reuse has not been verified. `gateway api-install --provider xai --model MODEL` adds
an explicitly selectable Grok custom model; `gateway api-run` runs it in the foreground,
and `gateway api-uninstall` restores the backed-up settings. API configuration, port,
token and encrypted state are separate from subscription mode. Only `https://api.x.ai`
is a production API origin. See the [Grok/API guide](GROK.md) and
[compatibility table](COMPATIBILITY.md) for the verified scope and API billing boundary.

Images, audio, binary/remote files, unknown request fields, WebSocket inference,
`previous_response_id`, background/server-side truncation and compaction endpoints are
unsupported and blocked. No raw pass-through exists. Telemetry, external MCP/browser/tool
traffic, cloud agents and manually overridden endpoints are outside this inference boundary.
This is not an OS-wide network filter or proof of anonymity.

Native **Claude Code 2.1.287** and **Codex 0.160.0** were tested on Windows with fake OAuth
and offline fixture providers. Tests verify masked prompts, split-response restoration,
restored filename tool arguments, actual file reads, masked source/email tool results,
stable mappings, OAuth forwarding and API-key refusal. Captured provider-bound requests
exclude the fixture function name and email. These fixture tests use no real subscription
credentials or model calls. Live tests also verify both agents in **English and Korean**,
using **Sonnet 5.5** and **gpt-6.1-sol** with existing subscription logins. Each test requires
a native file read, at least two inference requests, provider-bound source without the
fixture's function/parameter/email/comment, and a successful answer restored to the original
function name and email. The local file must remain unchanged. The former Claude 429 was
an attribution bug, not subscription exhaustion; neutral aliases also resolved the benign
masked-prompt refusal. No API keys were used, and host settings were not installed during
testing. Provider usage limits/entitlements still apply. macOS/Linux startup
registration has format tests, not real login tests. Windows' hidden WScript launcher was
also executed successfully with quoted paths; an actual OS login was not simulated.

Live tests are opt-in and consume the existing plan's allowance. Normal CI uses offline
fixtures. Set the native executable paths and run all four real-provider scenarios:

```powershell
$env:SECURE_MCP_LIVE_SUBSCRIPTION = '1'
$env:SECURE_MCP_CLAUDE_BINARY = 'C:/path/to/claude.exe'
$env:SECURE_MCP_CODEX_BINARY = 'C:/path/to/codex.exe'
python -m pytest tests/test_gateway_live.py -v
```

The test uses temporary fixture files and process-local endpoint overrides. Codex's test
process has unrestricted access to that fixture directory to avoid Windows sandbox setup
interfering with file-read verification; the installer preserves users' permission settings.

## 한국어 안내

처음 `secure-mcp gateway install`을 실행한 뒤 두 CLI를 재시작하면, 이후에는 평소처럼
`claude` / `codex`를 사용합니다. 매번 별도 명령이나 파일 목록을 입력할 필요가 없습니다.
직접 입력·코드·도구 결과는 전송 전에 치환하고, 화면의 답변과 로컬 도구 인자는 자동 복원합니다.
함수명·변수명·문자열·숫자·주석도 코드 마스킹 대상이며 입력과 코드의 참조는 같은 치환표를 사용합니다.

기존 구독 로그인 파일·키체인은 변경하지 않습니다. 인증과 갱신은 각 CLI가 맡고, 게이트웨이는
OAuth를 원래 서비스로 전달합니다. API 키는 거부하므로 유료 API로 자동 전환하지 않습니다.
구독 한도는 그대로 적용됩니다. 두 CLI 모두 영어·한국어 질문으로 실제 파일 읽기,
코드 마스킹 전송, 정상 답변 생성과 원래 함수명·이메일 자동 복원까지 검증했습니다.
Claude의 기존 429는 식별 블록 처리 오류였고, 정상 요청의 거절은 치환명을 중립적인
`private_symbol_` 형식으로 바꾸어 해결했습니다. 안전 필터와 사용자 문맥·코드 마스킹은 유지합니다.
0.5.0에서 업데이트하면 기존 상태 파일은 보존하며, 새 치환표를 쓰도록 대화를 한 번 새로 시작하세요.

새 Hook을 등록하지 않습니다. 운영체제 사용자 로그인 시 백그라운드로 자동 실행합니다.
해제는 `secure-mcp gateway uninstall`이며, 설치 뒤 사용자가 수정한 설정은 덮어쓰지 않습니다.
스트리밍 전체를 모아 복원하므로 표시가 늦어질 수 있습니다. 서명·암호화된 추론은 가명 상태를
유지합니다. 이미지·원격 첨부·미지원 요청은 차단합니다. 별도 도구 통신·텔레메트리는 보호 범위에
포함되지 않으며, 문법·코드 구조·알고리즘이 남으므로 완전한 익명성을 보장하지 않습니다.


## Recovery, concurrency, and supported constraints

Alias transformation and encrypted snapshot writes are serialized per provider; waiting
for the upstream network does not hold the alias lock or block another provider. Each
provider keeps its own in-memory snapshot encryption context, deriving the key once;
ordinary encrypted files still decrypt with the configured token. Expiry/reset/shutdown
release that context. In-flight requests retain their alias table until restoration ends.

The default mapping limit is 100,000. Set `mapping_limit` to a positive integer in the
local gateway JSON configuration, or use `gateway run --mapping-limit 200000` for a
foreground subscription daemon. The bound applies before allocating a new alias;
existing aliases remain usable at the limit. To recover deliberately:

```shell
secure-mcp gateway reset --agent claude
secure-mcp gateway reset --config /path/to/api-gateway.json --agent xai
```

Reset requires the local gateway token and refuses while the selected provider has
active requests. It deletes that provider's encrypted snapshot and reasoning provenance.
Start a new conversation afterwards: earlier conversation/tool arguments refer to the
old alias table. Other providers retain their mappings. Reset does not modify login files.

Native OS advisory locks release automatically after a process crash. Their marked
`.lock` files intentionally persist; do not delete them while writers may be running.
A legacy empty lock from the previous exclusive-create format is refused conservatively.
Stop **all old writers**, then remove only the confirmed legacy empty lock and retry.
Install/uninstall use the same configuration lock. An interrupted uninstall records
recovery state; retry uninstall to finish, preserving unexpected user edits.

JSON Schema named dependencies (`dependentRequired`, `dependentSchemas`, and legacy
`dependencies`) share aliases with `properties`. Regex `pattern` and `patternProperties`
constraints are rejected before forwarding because renaming private names cannot safely
preserve arbitrary regex semantics. Replace them with explicit supported properties.
Subscription controls and request envelopes are validated before private content is masked.

Upstream 400/401/403/404/408/413/422/429/500/502/503/504/529 statuses are preserved.
Only allowlisted public error types/codes are returned; provider messages, parameter names,
and other diagnostic content are discarded. Context-length errors receive a fixed
compaction hint. Unknown statuses and redirects become 502. Retry metadata remains bounded
to the existing public response-header allowlist. SSE is fully buffered (16 MiB maximum);
the 180-second socket timeout is a read timeout, **not a total request deadline**. These
limits can reject large or slow streams; reset is not a substitute for context compaction.


POST query metadata is forwarded without alteration after validation: `beta=true|false`,
`client_version` with dotted numeric components, and date-shaped `api-version` are supported.
Unknown, duplicate, or private query parameters are rejected before contacting the provider.
