import json
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from models import ChatRequest, ChatResponse,AgentOutput
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai import ModelRetry, RunContext

load_dotenv(Path(__file__).parent.parent / ".env")

model = OpenAIChatModel(
    os.environ["LLM_MODEL"],
    provider=OpenAIProvider(base_url=os.environ["LLM_BASE_URL"]),
)

mcp_toolset = MCPToolset(
    os.environ["MCP_SERVER_URL"],
    headers={"Authorization": os.environ["MCP_API_KEY"]},
)

agent = Agent(
    model,
    toolsets=[mcp_toolset],
    output_type=str,
    instructions=(
        "사용자 요청에 적합한 MCP 도구를 호출하고, "
        "반드시 llm이 요약하거나 재작성하지 않고 도구가 반환한 결과를 그대로 최종 응답으로 반환한다."
    ),
)

@agent.output_validator
def ensure_mcp_called(ctx: RunContext[None], output: str) -> str:
    mcp_was_called = any(
        isinstance(part, ToolReturnPart)
        for message in ctx.messages
        for part in message.parts
    )

    if not mcp_was_called:
        raise ModelRetry("답변하기 전에 MCP 도구를 호출하세요.")

    return output


def normalize_result(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value

app = FastAPI()

def find_last_tool_execution(
    messages: list[Any],
) -> tuple[ToolCallPart, ToolReturnPart] | None:
    calls: dict[str, ToolCallPart] = {}
    last_execution: tuple[ToolCallPart, ToolReturnPart] | None = None

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part

            elif isinstance(part, ToolReturnPart):
                tool_call = calls.get(part.tool_call_id)

                if tool_call is not None and part.outcome == "success":
                    last_execution = (tool_call, part)

    return last_execution

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    result = await agent.run(request.request)

    execution = find_last_tool_execution(result.all_messages())

    if execution is None:
        return ChatResponse(
            result=AgentOutput(
                status="not_found_tool",
                tool_name=None,
                missing_parameter=[],
                arguments={},
                tool_result=None,
            )
        )

    tool_call, tool_return = execution
    actual_tool_result = tool_return.content

    return ChatResponse(
        result=AgentOutput(
            status="ready",
            tool_name=tool_call.tool_name,
            missing_parameter=[],
            arguments=tool_call.args_as_dict(),
            tool_result=normalize_result(actual_tool_result),
        )
    )