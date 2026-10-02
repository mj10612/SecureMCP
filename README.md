# SecureMCP

Local English/Korean text masking and code review representations, with session-based restoration.

Version 0.3 adds local Claude Code hooks without an API gateway. Install with `uv tool install .`,
then run `secure-mcp init --agent claude` and `secure-mcp doctor`. This opt-in integration masks
supported tool text and restores local execution/display values; direct prompts, attachments,
telemetry and unknown output fields are not covered. See [local integration](docs/LOCAL_INTEGRATION.md)
for installation, removal, the `exec` wrapper and exact failure behavior.

## English

Mask confidential input **before** sending it to a provider. Restore responses in your trusted
local application and show them to the user there. Model-invoked MCP tool arguments are already
visible to the provider; adding this server to Claude Desktop or Cursor does not automatically
intercept or protect prompts. Restored originals must not be sent back into the model context.
This is heuristic masking, not a proof of anonymity, cryptographic zero knowledge, or regulatory compliance.

```bash
uv pip install -e ".[dev]"
python -m secure_mcp demo
```

```python
from secure_mcp import LocalPrivacyClient

# Replace echo with your provider adapter. Only its argument may leave the host.
def provider(masked_payload: str) -> str:
    return masked_payload

with LocalPrivacyClient() as client:
    result = client.request(
        "Patient John Doe received 50mg.", provider,
        sensitive_terms={"John Doe"},
    )
    print(result.unmasked_text)  # Local display only
```

### Policies and supported inputs

| Mode | Behavior |
| --- | --- |
| `content_words` | Mask content words and detected sensitive spans; keep functional grammar. |
| `entities_only` | Mask recognized PII, URLs, secrets, code spans, numbers and capitalized names. Korean and other case-free/non-ASCII words are masked conservatively, including ordinary nouns. |
| `aggressive` | Keep a small structural subset of articles/prepositions/conjunctions; mask auxiliaries, adverbs, pronouns and other words. |
| `code_aware` | Mask identifiers, numbers, literals and comments using the selected language lexer. |

English, Korean and mixed input are handled per word. Unicode names remain atomic. Korean
particle splitting uses known stems and conservative rules; unknown ambiguous words stay whole.
It is not a full morphological analyzer or universal person-name detector. Supply `sensitive_terms`
for domain names, lowercase names, ambiguous names, and custom secrets. `custom_preserve` / CLI
`--preserve` deliberately exempts selected words; recognized sensitive spans still take precedence.
Unknown secret formats and context-dependent names may evade `entities_only`; inspect the payload
or use broader masking. A high masking ratio does not prove absence of sensitive data.

Strategies: `bracket` (`[ENT_1]`), `unicode` (`⟦ENT_1⟧`), `pseudoword` (`⟪Brivel…⟫`),
and `hash` (random 96-bit nonce, independent of the original). Pseudowords use explicit delimiters
to avoid collisions with real words and adjacent tokens. Allocation is stable within a session;
random strategies intentionally differ between sessions. Keep all surrogate spelling intact.
Altered/unknown placeholder candidates are reported in `unmatched_surrogates`; `strict=True`
rejects them. Free-form deletion or invention by a model cannot always be detected or reconstructed.

Exact mask/unmask roundtrips keep written particles. For newly generated Korean responses,
opt into `normalize_particles=True` (CLI `--normalize-particles`) to choose 이/가, 은/는, 을/를,
과/와 and 으로/로 from a restored Hangul stem. Pronunciation of foreign names is not guessed.

Code languages: `python`, `javascript`, `typescript`, `go`, `rust`, `java`, `c`, `cpp`, `sql`.
Use explicit `language` in `mask_code` (CLI `--code-language`) for ambiguous snippets.
Auto detection is best effort. Keywords are language-specific; builtins and shadowed builtin
names are masked. Unicode identifiers, SQL comments/doubled quotes, backticks, Python f-strings
and escaped newlines, C++/Rust raw strings and numeric suffixes/separators are covered by tests.
Code allocations use ASCII names (`smcp_ID_n` and `smcp_LIT_n` inside literals)
and random native integer constants for numbers (reserved `732846` prefix plus 12 digits),
independently of the text strategy, so byte literals remain valid too. Python output
is syntax-checked in tests. Output is an opaque review representation: it need not execute,
type-check, retain numeric types/values, or preserve f-string/template interpolation behavior. This
small lexer is not a complete parser for every version of every supported language.

### CLI and sessions

```bash
python -m secure_mcp mask "Alice from Google" --session-id example --session-file example.enc --json-output
python -m secure_mcp unmask "[ENT_1] from [ENT_2]" --session-id example --session-file example.enc --strict
python -m secure_mcp mask "john doe" --mode entities_only --sensitive-term "john doe"
```

Use the same hidden password, or `SECURE_MCP_SESSION_PASSWORD`. CLI files use authenticated
Fernet encryption, random salt, PBKDF2-HMAC-SHA256 with 600,000 iterations, atomic writes and a
lock over the complete CLI read/modify/write operation. A concurrent writer fails clearly.
A crash may leave `example.enc.lock`; remove it only after confirming no writer is running.
Files are opt-in; without `--session-file`, mappings only survive in the current process.
Payload schema v2 stores allocations/counters without regenerating mappings. Legacy v1 files
remain readable; unknown schema versions fail explicitly. Delete session files after use.
Legacy bare pseudoword allocations retain their old ambiguity; start a new session to use
the safe delimited representation for every allocation.

Session strategies are immutable; `mode` records the last-used policy. Creating a duplicate ID
fails without changing its TTL or mappings. TTL must be positive and at most one day. The vault
sweeps idle expired sessions at most one cleanup interval later (default one second), and checks
expiration on access. Clear waits for active operations and invalidates retained references.
Library users must use `session.operation()` for custom mutations; built-in engine operations
use the same lock. `vault.close()` stops cleanup and releases all mapping references. Python
cannot promise byte-level memory zeroization. Standalone `PrivacySession` owners manage lifetime
themselves; use `SessionVault` for scheduled expiry.

`masked_ratio` counts masked token occurrences divided by non-whitespace/non-punctuation
occurrences. Deprecated `privacy_entropy_score` is an alias, not information entropy.
`restored_occurrences` counts replacements; `restored_unique_tokens` counts distinct allocations.
`restored_tokens_count` remains a compatibility alias for replacement occurrences.

### MCP utilities

`python -m secure_mcp serve` supports local stdio only. HTTP/SSE transports are disabled in the CLI;
directly exposing the Python server over a network requires host-provided authentication and isolation.
Tools: `mask_text`, `mask_code`, `create_privacy_session`, `get_session_stats`, `clear_privacy_session`.
Local Python `unmask_text` / `unmask_code` and CLI `unmask` are **not registered as MCP tools**,
so a model cannot enumerate mappings via restoration. Resources: `privacy://policies`, `privacy://status`.
Example desktop configurations are utility setups, not privacy proxies.

### Migration from 0.1

Version 0.2 changes pseudoword delimiters, code identifier/number representations, mapping keys
(`mapping_key(original, token_type, code=...)`), sentence-initial entity classification, keyword
preservation, and the MCP restoration boundary. Direct store readers should use mapping values
or `mapping_key`. Validate explicitly requested strategies; omitted strategies follow the generator.

## 한국어

0.3에서는 게이트웨이 없이 사용하는 로컬 Claude Code Hook을 추가했습니다. `uv tool install .`
설치 후 `secure-mcp init --agent claude`, `secure-mcp doctor`를 실행하세요. 지원하는 도구
결과의 텍스트를 마스킹하고 실행 인자·화면 표시를 로컬에서 복원합니다. 직접 입력·자동 첨부·
텔레메트리 등은 보호하지 않습니다. [설치·해제 및 보호 범위](docs/LOCAL_INTEGRATION.md)를 확인하세요.

SecureMCP는 영어·한국어·혼합 문장을 로컬에서 마스킹하고 복원합니다. 클라우드에 요청하기
**전에** `LocalPrivacyClient` 또는 로컬 API/CLI로 원문을 가리고, 응답은 로컬에서만 복원하세요.
모델이 호출하는 MCP 도구의 인자는 이미 제공자에게 전달되어 있으므로, Claude Desktop/Cursor에
서버를 추가하는 것만으로 개인정보가 보호되지는 않습니다. 복원한 원문을 모델 컨텍스트에 다시
넣으면 안 됩니다. MCP 복원 도구는 제거했으며, 기본 CLI 서버는 로컬 stdio만 지원합니다.

영어 대문자 이름과 Unicode 이름을 하나의 토큰으로 처리합니다. 한국어 이름과 일반 명사가
구분되지 않는 `entities_only`에서는 비기능어를 보수적으로 가립니다. 조사 분리는 알려진 어간과
보수적인 규칙을 사용하며, 모호한 미등록 단어는 통째로 가립니다. 모든 고유명사·비밀값 또는
한국어 형태소를 완벽히 인식하는 도구는 아닙니다. `sensitive_terms` / `--sensitive-term`으로
특정 용어를 지정하고, 알 수 없는 민감정보에는 더 넓은 마스킹 정책을 사용하세요.

정확한 왕복 복원에서는 입력 조사를 그대로 유지합니다. 모델이 새로 만든 문장에는
`normalize_particles=True` / `--normalize-particles`를 선택하여 받침에 맞는 조사를 보정할 수
있습니다. 영어 이름·약어의 발음은 추측하지 않습니다. `--strict`는 탐지한 변형·미등록
플레이스홀더의 복원을 거부하지만, 모델이 완전히 삭제한 내용을 재구성하지는 못합니다.

세션 전략은 생성 후 고정되며, 중복 ID 생성은 오류입니다. 만료 세션은 주기적으로 정리하고,
삭제와 진행 중 작업을 잠금으로 조율합니다. 별도 CLI 프로세스 간 복원에는 동일한 암호화
`--session-file`이 필요합니다. 마스킹 비율은 통계이며 기밀성·익명성·규제 준수의 증명이 아닙니다.

## Development

```bash
python -m ruff check src tests examples
python -m mypy src
python -m pytest -W error --cov=secure_mcp --cov-report=term-missing --cov-fail-under=85
```

[Architecture](docs/ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md) · [License](LICENSE)
