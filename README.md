# MCP Agent

Worker HTTP 요청을 받아 MCP Server의 `tools/list` 결과를 OpenAI function tools로 제공하고, 선택된 도구를 `tools/call`로 실행해 결과를 반환합니다.

## Setup

```powershell
cd D:\dev\workspace\Orchestration\mcp-agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env`에서 `MCP_SERVER_URL`, `OLLAMA_BASE_URL`, `OPENAI_MODEL`을 설정한 뒤 실행합니다.

```powershell
uvicorn mcp_agent.agent:app --host 0.0.0.0 --port 8000
```

## Worker API

`POST /worker`

```json
{ "request": "사용자 요청을 처리해줘" }
```

응답은 선택된 `tool_name`, `arguments`, MCP의 원본 `tool_result`를 포함합니다.

## MCP 연결 점검

> TODO: 전체 Worker 흐름 검증이 끝나면, 이 임시 점검 엔드포인트(`/mcp/tools`)는 삭제할 수 있습니다.

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/mcp/tools
```

두 번째 요청이 MCP Server의 도구 목록을 반환하면 `Agent → initialize → tools/list` 연결은 정상입니다. `POST /worker`는 Ollama 기반 도구 선택과 `tools/call`을 수행합니다.

MCP Server는 Streamable HTTP endpoint여야 합니다. stdio 연결이 필요한 경우 `MCPClient`에 별도 transport를 추가하세요.
