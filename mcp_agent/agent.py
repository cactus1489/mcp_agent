"""FastAPI orchestration endpoint: request -> choose MCP tool -> execute it."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from openai import AsyncOpenAI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .mcp_client import MCPClient

# 실행 디렉터리의 .env 값을 애플리케이션 설정으로 읽는다.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mcp_server_url: str = Field(alias="MCP_SERVER_URL")
    mcp_auth_token: str = Field(alias="MCP_AUTH_TOKEN")
    ollama_base_url: str = Field(alias="OLLAMA_BASE_URL")
    openai_model: str = Field(alias="OPENAI_MODEL")

settings = Settings()
mcp_client = MCPClient(settings.mcp_server_url, settings.mcp_auth_token)
mcp_tool_list: list[dict[str, Any]] = []

# 서버가 시작될 때 MCP 서버에서 도구 목록을 한 번 조회해서 전역 변수 mcp_tool_list에 저장하는 초기화 함수
# yield 이후부터 서버가 /worker 요청 처리
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global mcp_tool_list
    mcp_tool_list = await mcp_client.list_tools()
    yield

app = FastAPI(title="MCP Agent", version="0.1.0", lifespan=lifespan)

#powershell에서 한글깨짐 방지
@app.middleware("http")
async def add_utf8_charset(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")

    if content_type.startswith("application/json") and "charset=" not in content_type.lower():
        response.headers["content-type"] = f"{content_type}; charset=utf-8"

    return response

# tools/list 결과와 Worker 요청을 LLM에 주어 하나의 도구를 선택한다.
async def select_tool(
    request: str, tool_list: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    if not tool_list:
        raise HTTPException(status_code=502, detail="MCP server returned no tools")

    client = AsyncOpenAI(
        api_key="ollama",
        base_url=settings.ollama_base_url,
    )
    response = await client.responses.create(
        model=settings.openai_model,
        input=request,
        instructions=(
            "Select exactly one function that can fulfill the worker request. "
            "Supply only arguments accepted by its JSON schema."
        ),
        tools=tool_list,
        tool_choice="required",
        parallel_tool_calls=False,
    )
    function_call = next(
        (item for item in response.output if item.type == "function_call"), None
    )
    arguments = json.loads(function_call.arguments)
    return function_call.name, arguments

@app.post("/execute")
async def handle_execute_request(request: Request) -> dict[str, Any]:
    """Receive a Worker instruction and return the selected MCP tool result."""
    try:
        worker_request = (await request.json())["request"]

        # １) LLM이 요청에 맞는 도구와 JSON 인자를 선택한다.
        tool_name, arguments = await select_tool(worker_request, mcp_tool_list)

        # ２) 선택된 MCP 도구를 실행하고 Worker에 결과를 반환한다.
        result = await mcp_client.call_tool(tool_name, arguments)
        worker_response = {
            "tool_name": tool_name,
            "arguments": arguments,
            "tool_result": result,
        }
        return worker_response
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"MCP operation failed: {error}") from error