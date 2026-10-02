# Local Claude Code integration / 로컬 Claude Code 연동

SecureMCP 0.3 adds opt-in, local command hooks without an API gateway, HTTP service,
or provider endpoint changes. This is an experimental **tool-text integration**, not
a guarantee that all confidential data stays off the network. The project name remains SecureMCP.

## Install and remove

Install the package in an environment that will remain available to Claude Code:

```powershell
uv tool install .
secure-mcp init --agent claude
secure-mcp doctor
```

The default scope is the current project: `.claude/settings.local.json`. Use `--global`
on both `init` and `doctor` for `~/.claude/settings.json`. `--state-dir PATH` selects a
different local state directory; pass the same path to installation, diagnostics and
manual commands. Restart Claude Code after installation or removal.

```powershell
secure-mcp init --agent claude --global
secure-mcp doctor --agent claude --global
secure-mcp uninstall --agent claude --global
```

Installation merges our four command hooks into the existing registry. Other hooks,
permissions and settings are retained. Existing settings receive an exact byte backup
named `settings.json.secure-mcp-<timestamp>.bak` (or the project equivalent).
Repeated installation replaces our entries rather than duplicating them. Invalid JSON
or invalid hook registries are refused. Removal deletes only our registrations, retaining
encrypted state and backups. It never restores an old entire settings file over newer changes.

The registered command uses the absolute installing Python executable, so moving/removing
that environment requires reinstalling the hooks. `doctor` checks registrations and key
presence; it does **not** certify host-version compatibility or inspect actual provider traffic.
Claude Code must support `updatedToolOutput` and `MessageDisplay`. Check the
[current official hook reference](https://code.claude.com/docs/en/hooks), then test the
installed host with a disposable, nonsensitive fixture before relying on the integration.

## Data flow and scope

1. `PostToolUse` masks recognized text fields in successful tool results with the existing
   `entities_only` engine, using Unicode placeholders such as `⟦ENT_1⟧`.
2. `PreToolUse` restores string argument values for local `Bash`, `PowerShell`, `Read`,
   `Grep`, `Glob`, `Edit`, `Write` and `MultiEdit` tools before execution. It never returns
   an approval decision on success. Normal host permissions remain necessary.
3. `MessageDisplay` restores assistant text for display only. The replacement does not
   change the model's response history. Whole-line deltas supplied by the host are used;
   this is not a generic arbitrary-chunk streaming adapter.
4. `SessionEnd` deletes that conversation's encrypted snapshot. No mappings are exposed
   through MCP restoration tools.

JSON keys, numeric/boolean scalars and protocol fields are preserved. Recognized text fields
are `stdout`, `stderr`, `text`, `content`, `output`, `filePath`, `file_path`, `filename`,
`filenames`, `matches`, `line`, `lines` and `message`. Top-level strings/lists are also handled.
Binary data, signatures, IDs, type and MIME discriminators are preserved. Unknown fields,
images, PDFs, numeric JSON values and other opaque data are **not protected**. This schema-aware
selection avoids corrupting tool protocols; new tool schemas require an adapter and validation.
This policy is heuristic, conservative for Korean, and can reduce reasoning quality. It does
not preserve all source-code semantics or provide universal secret detection.

Restoration is restricted to the listed local tools. A surrogate-bearing remote MCP/network
tool call is refused rather than receiving restored originals. Local commands can themselves
use a network, so hooks are not a network sandbox. Keep host permissions and network controls.

### Important uncovered paths and failure behavior

- Direct user prompts, rules/system instructions, `@file` attachments, automatic context,
  tool schemas, failed-tool responses and history loaded before installation are not intercepted.
  `UserPromptSubmit` cannot replace the submitted prompt. Do not paste secrets expecting this
  integration to rewrite them.
- Host telemetry can record original tool output **before** `PostToolUse`. Local transcripts,
  restored execution arguments, the tool execution itself, and other host context-building
  paths are outside this protection boundary. There is no all-traffic privacy claim.
- A malformed replacement schema can be ignored by the host. Hook errors/timeouts can also
  cause original output to be used. A post-execution hook cannot fail closed against all leaks.
  This integration must not be used as the sole enforcement layer for confidential workloads.
- Missing/expired/corrupt mappings, unknown placeholders or concurrent state writes refuse
  restoration. `PreToolUse` returns a deny decision with a sanitized error. Display failures
  leave surrogate text visible. No raw input or error candidates are echoed by the CLI.
- Other output-rewriting hooks may replace our result. Test the final model-visible payload
  when combining RTK or other integrations; registration alone does not prove protection.

## Local command wrapper

For commands whose output must be masked **before the host receives it**, invoke the local
wrapper explicitly (or through your own validated command-rewrite integration):

```powershell
secure-mcp exec --session-id example -- git status
secure-mcp stats --session-id example
```

The wrapper accepts an executable and argument list, with no implicit shell. For pipelines,
invoke a shell explicitly. It restores known placeholders in the arguments, captures UTF-8
stdout/stderr locally, masks them before returning either stream, and preserves the exit code.
Masking failure returns no raw captured output. Execution is buffered, not interactive streaming;
large/unbounded output is unsuitable. Match the host's `session_id` if using the same mappings
for hooks. `init` does not automatically rewrite shell commands: doing so changes permission
matching and shell behavior and needs a separate host-specific validation.

## State and lifecycle

Hook subprocesses share authenticated, encrypted session snapshots under
`~/.secure-mcp/hooks` by default. A random local password is stored in `key` beside them;
it is never included in hook settings. The existing encrypted-session transaction lock prevents
concurrent updates; a conflicting writer fails without overwriting mappings. Session filenames
are hashes of host conversation IDs, preventing path traversal.

Mappings expire after one hour idle. An expired session is refused, not silently reallocated:
old placeholders must not acquire new meanings. Start a new conversation to get a new host ID.
Clean up orphan snapshots after a host crash; disk expiry is validated on access rather than
by a background daemon. A crashed transaction may leave a `.lock` file: remove it only after
confirming no writer is active. Removal retains state so an ongoing conversation can still be
restored manually. Delete the state directory after all conversations are finished if desired.

The key and snapshots are protected by the local OS account. POSIX creation requests private
permissions; Windows uses inherited ACLs, which must be suitable for private user files.
Encryption with a colocated key does not defend against another process running as the same user,
a malicious agent permitted to read these files, or a compromised machine. Backups inherit the
same local trust requirement. No physical memory-zeroization guarantee is made.

## 한국어 사용 안내

이름은 SecureMCP로 유지합니다. 로컬에 설치한 뒤 `secure-mcp init --agent claude`를
실행하면 프로젝트의 Claude Code Hook을 등록합니다. API 주소 변경이나 게이트웨이 서버는
필요하지 않습니다. 전체 프로젝트에 적용하려면 `--global`을 사용하세요. `doctor`는 설정과
키 존재 여부를 검사하며, 실제 호스트의 동작을 보증하지는 않습니다.

성공한 도구 실행 결과의 지원 텍스트 필드를 마스킹하고, 다음 로컬 도구 실행 전에 치환값을
복원합니다. 화면에 표시하는 답변은 원문으로 복원하되 모델 응답 기록은 치환된 상태로 둡니다.
영어·한국어·혼합 문장을 지원하지만 한국어의 보수적인 마스킹은 일반 명사까지 가릴 수 있습니다.
기존 권한 설정과 다른 Hook을 유지하며, 설치 전 설정은 백업하고 해제 시에는 우리 Hook만 제거합니다.

**직접 입력, 자동 첨부, 시스템 지시문, 미지원 필드, 이미지, 실패한 도구 결과, 텔레메트리는
보호 범위에 포함되지 않습니다.** Hook 오류나 호스트 출력 형식 변경으로 원문이 전달될 수
있으므로 전체 개인정보 유출 방지 수단으로 사용하면 안 됩니다. 실행 결과를 호스트에 전달하기
전에 가리려면 `secure-mcp exec --session-id <ID> -- <실행파일> <인자>`를 사용하세요.
이 명령은 현재 자동 등록되는 명령 재작성 기능이 아닙니다.

치환표는 대화 ID별로 암호화하여 저장하고 별도 Hook 프로세스가 공유합니다. 로컬 키가 같은
폴더에 있으므로 같은 사용자 권한의 프로세스까지 차단하지는 않습니다. 대화 종료 시 치환표를
삭제하며, 비정상 종료 후 남은 파일과 백업은 사용자가 정리해야 합니다. 알 수 없는 치환값,
만료·손상된 상태 또는 동시 쓰기가 발생하면 실행 인자 복원을 거부합니다. 원격 도구에는 원문을
복원해 전달하지 않습니다. 실제 Claude Code 버전에서 민감하지 않은 테스트 자료로 동작을 먼저 확인하세요.
