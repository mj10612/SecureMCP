# Automatic subscription integration

SecureMCP 0.5 provides an experimental authenticated loopback gateway for Claude Code and
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
detection. Ordinary text uses `content_words`, known terms and uppercase single-letter
references. This remains heuristic: ambiguous/unfenced or unsupported code needs clearer
context. Grammar, syntax, public built-in tool names and protocol IDs stay visible. Masking
does not hide algorithms or inferred meaning; it can reduce reasoning/edit quality.

Common review/task vocabulary is retained in ordinary instructions to keep requests usable;
known source identifiers take priority over that vocabulary. Code lexing has no identifier
preservation exception. Core tool descriptions use fixed public descriptions, and an explicit
privacy instruction tells the model to keep supplied aliases unchanged.

JSON replies and SSE text/tool-argument deltas are restored. Streams are buffered until
complete so split aliases can be folded before restoration. This adds latency and removes
live token-by-token display. Unknown/altered aliases fail rather than inventing originals.
Schema properties and tool-argument JSON keys share aliases. Only pinned public Codex
`exec` / `apply_patch` grammars pass unchanged; other custom grammars are blocked. Claude's
safety classifier keeps public policy tags while private paths/rule operands are masked.
Operational schema fields and
JSON scalar numeric parameters retain their types; numbers in source text are masked.

Signed Claude thinking and encrypted Codex reasoning remain exactly masked and are not
restored for display. Only blocks previously emitted by this gateway can be replayed.
Claude/Codex have separate tables, shared across this installation's conversations/accounts;
requests per agent are serialized. Use separate configurations/ports for separate trust domains.

Encrypted snapshots reuse SecureMCP's authenticated encryption and password KDF. They preserve
aliases across daemon restart. Reasoning provenance stores only hashes. Snapshots expire after
24 hours idle, but files are not automatically deleted. Expired/corrupt reasoning state blocks
replay; start a fresh conversation/reset state rather than bypass masking. POSIX private file
modes are applied; Windows uses inherited user ACLs. The key/local token resides alongside
snapshots, so this does not protect against another process with access to that user's files.

## Protection boundary and tests

Supported routes: Anthropic Messages/token counting, Codex Responses/model catalog.
Only IPv4 loopback can bind; a local access token is mandatory. Production upstreams are
fixed HTTPS origins; redirects and environment proxies are disabled when forwarding OAuth.
Requests/replies are bounded to 16 MiB; upstream response reads time out after 180 seconds.

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
credentials or model calls. Separately, a live **Codex 0.160.0** smoke test passed with the
existing ChatGPT login and restored an identifier automatically. A live **Claude 2.1.287**
attempt reached Anthropic but returned HTTP 429; actual Claude model response/restoration
with that login remains unverified. No API keys were used, and host settings were not installed
during testing. Provider usage limits/entitlements still apply. macOS/Linux startup
registration has format tests, not real login tests. Windows' hidden WScript launcher was
also executed successfully with quoted paths; an actual OS login was not simulated.

## 한국어 안내

처음 `secure-mcp gateway install`을 실행한 뒤 두 CLI를 재시작하면, 이후에는 평소처럼
`claude` / `codex`를 사용합니다. 매번 별도 명령이나 파일 목록을 입력할 필요가 없습니다.
직접 입력·코드·도구 결과는 전송 전에 치환하고, 화면의 답변과 로컬 도구 인자는 자동 복원합니다.
함수명·변수명·문자열·숫자·주석도 코드 마스킹 대상이며 입력과 코드의 참조는 같은 치환표를 사용합니다.

기존 구독 로그인 파일·키체인은 변경하지 않습니다. 인증과 갱신은 각 CLI가 맡고, 게이트웨이는
OAuth를 원래 서비스로 전달합니다. API 키는 거부하므로 유료 API로 자동 전환하지 않습니다.
구독 한도는 그대로 적용됩니다. Codex는 실제 구독 요청과 자동 복원에 성공했습니다.
Claude는 사용량 제한(HTTP 429)으로 실제 모델 응답 검증을 완료하지 못했습니다.

새 Hook을 등록하지 않습니다. 운영체제 사용자 로그인 시 백그라운드로 자동 실행합니다.
해제는 `secure-mcp gateway uninstall`이며, 설치 뒤 사용자가 수정한 설정은 덮어쓰지 않습니다.
스트리밍 전체를 모아 복원하므로 표시가 늦어질 수 있습니다. 서명·암호화된 추론은 가명 상태를
유지합니다. 이미지·원격 첨부·미지원 요청은 차단합니다. 별도 도구 통신·텔레메트리는 보호 범위에
포함되지 않으며, 문법·코드 구조·알고리즘이 남으므로 완전한 익명성을 보장하지 않습니다.
