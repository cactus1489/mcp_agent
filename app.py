import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

load_dotenv(Path(__file__).with_name(".env"))

model = OpenAIChatModel(
    os.environ["LLM_MODEL"],
    provider=OpenAIProvider(
        base_url=os.environ["LLM_BASE_URL"], 
    ),
)

mcp_toolset = MCPToolset(
    os.environ["MCP_SERVER_URL"],
    headers={"Authorization": os.environ["MCP_API_KEY"]},
)

class ChatRequest(BaseModel):
    question: str = Field(min_length=1)

class ChatResponse(BaseModel):
    status: Literal["ready", "missing_parameter", "not_found_tool"]
    tool_name: str
    missing_parameter: list[str]
    arguments: dict[str, Any]
    tool_result: dict[str, Any] | None = None

agent = Agent(
    model,
    toolsets=[mcp_toolset],
    output_type=ToolOutput(
        ChatResponse,
        name="return_chat_response",
    ),
    instructions="""
        사용자 질문에 맞는 MCP 도구를 선택한다.

        - 필수 파라미터는 inputSchema.required만 기준으로 판단하며, description은 필수 여부 판단에 사용하지 않는다.
        - missing_parameter에는 누락된 required의 정확한 property 이름만 넣는다.
        - required가 없거나 비어 있으면 선택한 MCP 도구를 호출한다.
        - 필수값을 사용자 질문에서 확인할 수 없으면 추측하지 않는다.
        - 필수값이 부족하면 도구를 호출하지 않고 return_chat_response를 status="missing_parameter"로 호출한다.
        - 적합한 도구가 없으면 return_chat_response를 status="not_found_tool"로 호출한다.
        - MCP 도구 호출 후 반드시 return_chat_response를 호출한다.
        - MCP 호출 성공 시 status는 "ready"로 반환한다.
        - ready일 때 tool_name은 실제 호출한 도구명, arguments는 실제 호출 파라미터,
          missing_parameter는 빈 배열, tool_result는 MCP 서버 결과 전체로 반환한다.
        - MCP 결과를 요약하거나 자연어로 변환하지 않는다.
        """,
)

app = FastAPI()

@app.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def chat(request: ChatRequest) -> ChatResponse:
    result = await agent.run(request.question)
    return result.output
