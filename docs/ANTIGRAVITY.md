# Antigravity 연결과 검증 범위

검증일: 2026-10-07. 로컬 환경: Windows 11 build 26200,
Antigravity CLI `agy --version` = `1.3.0`, Python 3.11.
SDK 로컬 endpoint probe는 `google-antigravity==0.1.20`을 별도 환경에서 사용했다.

## CLI의 stdio MCP 연결

먼저 SecureMCP가 설치된 Python의 절대 경로를 확인한다. PowerShell 예:

```powershell
$secureMcpPython = (Resolve-Path .venv\Scripts\python.exe).Path
agy mcp list
agy mcp add secure-mcp $secureMcpPython -m secure_mcp serve
agy mcp list
```

CLI 1.3.0은 사용자 `~/.gemini/config/mcp_config.json`을 수정한다.
[공식 MCP 문서](https://antigravity.google/docs/mcp/)는 사용자 설정과
프로젝트 `.agents/mcp_config.json`, `mcpServers` 구조를 설명한다.
[설정 예제](../examples/antigravity_mcp_config.json)의 `command`를 실제
Python 절대 경로로 바꾸면 PATH에 의존하지 않는다. 경로에 공백이 있어도
command 문자열 하나와 args 배열을 유지한다.

`agy mcp add`는 같은 이름의 기존 서버를 **덮어쓴다**. 설치 전 설정 파일을
백업하고 `secure-mcp`가 이미 있으면 해당 항목을 보존한다. 다른 서버
항목은 유지된다. 제거:

```powershell
agy mcp remove secure-mcp
```

제거는 덮어쓴 이전 항목을 복원하지 않는다. 기존 `secure-mcp` 항목이
있었다면 백업에서 그 항목만 복원하고, 설치 이후 추가된 다른 서버는 유지한다.
SecureMCP는 Gemini/Antigravity 인증 파일을 읽거나 저장하지 않는다.

자동 검증은 격리된 HOME/USERPROFILE에서 add/list/remove, 기존 서버 보존,
동명 항목 교체, 공백 경로를 확인한다. 등록된 command/args를 실제 MCP
클라이언트로 실행해 initialize, list_tools, mask_text를 검증한다.
이 offline 테스트는 모델 추론이나 구독을 사용하지 않는다. Antigravity
모델 자체의 도구 선택 검증은 별도의 opt-in live 테스트이며 기본 실행에서 제외된다.

## 보호 경계

stdio MCP는 마스킹 도구를 제공한다. Antigravity의 기본 프롬프트, 파일 읽기,
Gemini 추론 요청 전체를 자동 보호하지 않는다. 모델이 `mask_text`를 호출하면
그 도구의 원문 인자는 이미 제공자에게 보일 수 있다. 전체 게이트웨이 지원으로
표시하지 않는다. 기본 Gemini 구독의 endpoint 변경이나 인증 재사용을 제공하지 않는다.

## SDK endpoint 조사

[공식 SDK local-models 문서](https://antigravity.google/docs/sdk/local-models/)의
`LocalOpenAIAgentConfig(model=..., base_url=...)`는 로컬 OpenAI 호환 endpoint를
사용한다. SDK 0.1.20을 실제 실행하고 loopback HTTP fixture로 요청을 받은 결과:

- 경로: `/v1/chat/completions`, `stream: true`.
- 요청 필드: `model`, `messages`, `tools`, `tool_choice`, `max_completion_tokens`, `stream`.
- fixture의 HTTP 400은 SDK 실행 오류로 전달된다.

이 검증은 자격증명 없이 로컬 서버만 사용했다. SDK가 endpoint override를
지원한다는 증거이며, 기본 IDE/CLI Gemini 구독의 전송 경로가 같다는 증거는 아니다.
로컬 fixture 비용은 없다. 외부 제공자로 연결할 경우 해당 제공자의 인증 및
요금 조건을 적용해야 하며 기존 Gemini 구독 자격증명을 임의 재사용하면 안 된다.

아직 SDK 게이트웨이 지원을 완료하지 않았다. 실제 SDK의 도구 호출/결과,
stream 이벤트, 인증 헤더, compaction 요청 전체를 fixture로 수집하고 허용
스키마를 검증해야 한다. 이후 영어·한국어 질문 → 파일 읽기 → 제공자 payload
마스킹 → 로컬 답변/도구 인자 복원 계약을 통과해야 지원을 주장할 수 있다.

직접 연결의 구체적 장애물은 인증 헤더 설정이다. SDK 0.1.20의
[`LocalOpenAIAgentConfig` 구현](https://github.com/google-antigravity/antigravity-sdk-python/blob/main/google/antigravity/connections/local/local_openai_connection_config.py)은
`ModelTarget`에서도 이름과 base_url만 추출하며, 실제
[`LocalOpenAIConnectionStrategy`](https://github.com/google-antigravity/antigravity-sdk-python/blob/main/google/antigravity/connections/local/local_openai_connection.py)는
`GemmaEndpoint(base_url=...)`만 만든다. custom inference HTTP header/API key
설정은 제공하지 않는다. 실제 요청에도 SecureMCP 로컬 토큰 헤더가 없다.
따라서 gateway가 필수로 요구하는 `X-SecureMCP-Token`과 제공자 인증을
지원된 SDK API로 함께 전달하는 경로를 아직 확보하지 못했다. 연결을 위해
로컬 토큰 인증을 제거하지 않는다. SDK의 공식 header 지원 또는 인증과
세션 소유권을 보존하는 별도의 검증된 통합이 선행되어야 한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_antigravity_host.py -q
# SDK probe를 포함하는 별도 환경 (기본 프로젝트 의존성은 변경하지 않음)
uv run --isolated --with google-antigravity==0.1.20 --with pytest --with pytest-asyncio python -m pytest tests/test_antigravity_host.py -q
```
