"""FastAPI orchestration endpoint: request -> choose MCP tool -> execute it."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import FastAPI, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .mcp_client import MCPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    )
    logger.addHandler(console_handler)
logger.propagate = False

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

class AgentDecision(BaseModel):
    status: Literal["ready", "missing_parameter", "tool_not_found"]
    tool_name: str | None = None
    missing_parameter: list[str] = Field(
        default_factory=list
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict
    )

# 서버가 시작될 때 MCP 서버에서 도구 목록을 한 번 조회해서 전역 변수 mcp_tool_list에 저장하는 초기화 함수
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global mcp_tool_list
    mcp_tool_list = await mcp_client.list_tools()
    yield

app = FastAPI(title="MCP Agent", version="0.1.0", lifespan=lifespan)

# tools/list 결과와 execute 요청을 LLM에 주어 하나의 도구를 선택한다.
async def select_tool(
    request: str, tool_list: list[dict[str, Any]]
) -> AgentDecision:

    logger.info("LLM input request: %s", request)
    logger.info(
        "LLM input tools: %s",
        json.dumps(tool_list, ensure_ascii=False),
    )

    client = AsyncOpenAI(
        api_key="ollama",
        base_url=settings.ollama_base_url,
    )
    response = await client.responses.create(
        model=settings.openai_model,
        input=request,
        instructions=(
            """
                ## 목적
                    사용자 질의를 분석하여 적절한 도구를 선택하고, 도구 실행에 필요한 필수 파라미터의 충족 여부를 판단한다.

                ## 입력
                    Agent는 다음 정보를 제공한다.
                    - user_query: 사용자 질의
                    - tools: 사용 가능한 도구 목록
                    - name: 도구명
                    - description: 도구 설명
                    - parameters: JSON Schema 형식의 입력 파라미터 정의
                    - parameters.required: 필수 파라미터명 목록

                ## 제약
                    - 사용자 질의와 일치하는 도구 중 가장 적합한 하나를 선택한다.
                    - 필수 파라미터가 모두 명확하면 status를 "ready"로 설정한다.
                    - 필수 파라미터가 하나 이상 없거나 모호하면 status를 "missing_parameter"로 설정하고, 해당 파라미터명을 missing_parameter에 모두 넣는다.
                    - 필수 파라미터는 도구 입력 스키마의 required 배열에 명시된 항목만 인정하며, required에 없거나 required 배열 자체가 없으면 설명을 근거로 필수값을 임의로 추론하지 않는다.
                    - 대응하는 도구가 없으면 status를 "tool_not_found"로 설정한다.
                    - 사용자 질의에 없는 값은 추측하거나 임의로 생성하지 않는다.
                    - arguments에는 사용자 질의에서 명확히 확인된 값만 넣고, 파라미터명과 데이터 타입은 도구 정의를 따른다.
                    - status가 "ready"이면 missing_parameter는 []로 설정한다.
                    - status가 "tool_not_found"이면 tool_name은 "", missing_parameter는 [], arguments는 {}로 설정한다.
                    - 반드시 유효한 JSON 객체 하나만 출력하며, Markdown, 설명 문장, ```json, ``` 코드 블록을 절대 포함하지 않는다.
                    
                ## 출력
                    {
                        "status": "ready | missing_parameter | tool_not_found",
                        "tool_name": "선택된 도구명 또는 빈 문자열",
                        "missing_parameter": [],
                        "arguments": {}
                    }
            """
            f"사용 가능한 도구와 각 입력 스키마: "
            f"{json.dumps(tool_list, ensure_ascii=False)}"
        ),
    )

    logger.info("LLM raw output: %s", response.output_text)
    decision = AgentDecision.model_validate_json(response.output_text)
    logger.info("Agent decision: %s", decision.model_dump())
    return decision

@app.post("/execute")
async def handle_execute_request(request: Request) -> dict[str, Any]:
    """Receive a execute instruction and return the selected MCP tool result."""
    execute_request = (await request.json())["request"]

    # １) LLM이 요청에 맞는 도구와 JSON 인자를 선택한다.
    decision = await select_tool(execute_request, mcp_tool_list)
    logger.info("MCP execution candidate: %s", decision.model_dump())

    # LLM이 결정한 status가 ready일 때만 MCP 도구를 실행한다.
    if decision.status != "ready":
        return decision.model_dump(exclude_none=False)

    # ２) 선택된 MCP 도구를 실행하고 execute에 결과를 반환한다.
    result = await mcp_client.call_tool(decision.tool_name, decision.arguments)

    execute_response = {
        "status": decision.status,
        "tool_name": decision.tool_name,
        "missing_parameter": [],
        "arguments": decision.arguments,
        "tool_result": result,
    }
    return execute_response
