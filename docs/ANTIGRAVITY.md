# Antigravity 연결과 검증 범위

검증일: 2026-10-08. 로컬 환경: Windows 11 build 26200,
Antigravity CLI `agy --version` = `1.3.0`, Python 3.11.
SDK native tool 계약은 `google-antigravity==0.1.21`을 별도 환경에서 사용했다.

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

## SDK의 보호된 xAI API 연결

검증 버전: `google-antigravity==0.1.21`, Python 3.11, Windows 11 build 26200.
[공식 SDK local-models 문서](https://antigravity.google/docs/sdk/local-models/)의
`LocalOpenAIAgentConfig(model=..., base_url=...)`를 사용한다. 이 경로는
**명시적인 xAI API 모드**이며 Gemini 구독을 재사용하지 않는다. 외부 xAI
연결에는 사용자 API key와 xAI의 별도 요금이 적용된다. offline fixture는
loopback 서버만 사용하며 결제나 실제 제공자 호출이 없다.

SDK 0.1.21은 실제 `/v1/chat/completions` streaming 요청을 사용하지만,
[`LocalOpenAIAgentConfig` 구현](https://github.com/google-antigravity/antigravity-sdk-python/blob/main/google/antigravity/connections/local/local_openai_connection_config.py)과
[`LocalOpenAIConnectionStrategy`](https://github.com/google-antigravity/antigravity-sdk-python/blob/main/google/antigravity/connections/local/local_openai_connection.py)는
`GemmaEndpoint(base_url=...)`만 전달한다. 따라서 mandatory gateway header를
설정하기 위해 SecureMCP의 context helper를 사용한다.

```python
import asyncio
import os
from pathlib import Path

from google.antigravity import Agent, LocalOpenAIAgentConfig
from secure_mcp.antigravity_sdk import antigravity_endpoint
from secure_mcp.xai_install import default_api_config, load_api_config

async def main():
    # 먼저 명시적인 xAI API gateway를 api-run으로 실행한다.
    config = load_api_config(default_api_config())
    api_key = os.environ["XAI_API_KEY"]
    with antigravity_endpoint(
        f"http://127.0.0.1:{config['port']}", config["token"], api_key
    ) as base_url:
        sdk_config = LocalOpenAIAgentConfig(
            model=os.environ["XAI_MODEL"], base_url=base_url,
            workspaces=[str(Path.cwd())],
            # SDK 자식 프로세스에 ambient API key를 전달하지 않는다.
            env={"XAI_API_KEY": ""},
        ).lightweight()
        async with Agent(sdk_config) as agent:
            response = await agent.chat("Read fixture.py and review the code.")
            async for token in response:
                print(token, end="", flush=True)

asyncio.run(main())
```

API gateway 설정과 실행은 [GATEWAY.md](GATEWAY.md)의 API 모드를 참고한다.
SDK는 optional dependency이므로 프로젝트의 기본 의존성을 바꾸지 않는다.
SDK 실행 환경에 `google-antigravity==0.1.21`을 별도로 설치한다.
기존 SDK tool policy·작업 공간·local history 설정은 호출자가 유지한다.
helper는 sandbox나 도구 권한을 승인하지 않는다.

### 인증·수명·지원 범위

helper는 context 동안만 IPv4 loopback listener를 실행한다. `base_url` 안의
무작위 256-bit capability는 bearer secret이다. 공유하거나 로깅하지 않는다.
listener는 capability를 constant-time 비교로 검증한 뒤에만 body를 읽으며,
`POST /v1/chat/completions`만 허용한다. 이 capability는 helper의 메모리에만
존재하고 helper는 설정 파일에 저장하지 않는다. SDK에 전달되는 설정도
일시적이므로 context 종료 후 URL은 사용할 수 없다. 예외가 발생해도 listener와
요청 worker를 닫는다.

helper는 SDK에 gateway token이나 제공자 key를 전달하지 않는다. 승인된 요청만
고정된 local gateway `/xai/v1/chat/completions`로 전달하며, 이때 gateway가
필수로 요구하는 `X-SecureMCP-Token`과 xAI 인증을 포함한다. gateway에서 마스킹한
payload만 고정 xAI origin으로 나간다. 자동 재시도, proxy 환경변수 사용,
redirect, 직접 제공자 fallback은 없다. 요청/응답 크기를 제한하고 오류 본문은
generic 메시지로 대체하여 token·provider 오류·원문이 SDK 로그에 반사되지 않는다.

영어·한국어 offline 계약은 실제 SDK native `view_file` 호출로 임시 source를
읽는다. provider fixture에서 두 요청의 private 함수명·변수명·이메일 부재를
검증하며, 분할 SSE tool 이름/JSON 인자 복원으로 실제 파일 읽기가 실행되고,
분할 SSE 답변이 로컬 SDK에 원래 이메일로 복원된다. SDK local history fixture에
capability가 저장되지 않는 것도 확인한다. SDK의 native JSON Schema와 tool
인자는 이 계약에 포함된다.

잘못된 capability·gateway token, unknown top-level payload, encoded/oversized
요청, 지원하지 않는 compaction control과 `/responses/compact`는 forwarding 없이
거절한다. SDK가 일반 Chat Completions 메시지로 보내는 문맥은 동일한 마스킹
경계를 사용하지만, 별도 opaque compaction protocol은 지원하지 않는다.
Gemini IDE/CLI 기본 구독 추론은 자동 redirect되지 않으며 기존 stdio MCP utility
범위를 유지한다.

이 helper는 SDK의 별도 telemetry·crash report·SDK 자체의 local history를
제어하지 않는다. capability URL을 SDK debug logging에 노출하지 않도록 SDK
설정을 검토하고, 제공자 key가 ambient 환경변수에 있다면 위 예제처럼 SDK
환경에서 비운다. Gemini/Antigravity login cache를 읽거나 저장하거나 재사용하지 않는다.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_antigravity_host.py -q
# 실제 SDK와 native tool 계약을 포함한 별도 환경
uv run --isolated --with google-antigravity==0.1.21 --with pytest --with pytest-asyncio python -m pytest tests/test_antigravity_host.py -q
```
