import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic_ai import Agent, ToolOutput, UsageLimits
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .models import AgentOutput, ChatRequest
from .logging_utils import logger, log_mcp_execution
from contextlib import asynccontextmanager

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
      output_type=ToolOutput(
          AgentOutput,
          name="return_agent_output",
          description="최종 결과 반환용 도구다. "
          "반드시 이 도구를 호출해 응답하며 일반 텍스트로 답하지 않는다."
      ),
      retries={"output": 3},#실패를 감지하고 정해진 횟수만큼 재시도하도록 관리(하네스)
      model_settings={"temperature": 0},
      #입력값 누락 여부 판단 및 추가 질문 유도
      instructions=
        """
            사용자 요청과 MCP 도구 설명을 비교하여 가장 적절한 도구를 선택한다.
            관련 도구가 없으면 status="not_found_tool"로 반환한다.
            관련 도구는 있지만 호출에 필요한 인자가 부족하면 도구를 호출하지 않고 status="missing_parameter"로 반환한다.
            missing_parameter에는 사용자가 추가로 제공해야 하는 input schema의 property key만 그대로 넣는다.
            사용자 입력이나 이전 도구 결과에 없는 인자 값은 추측, 생성 또는 임의 변환하지 않는다.
            검색 결과가 비어 있어도 조건을 변경하여 같은 도구를 다시 호출하지 않는다.
            도구 호출에 성공하면 status="ready"로 반환한다.
            tool_name에는 선택하거나 호출한 MCP 도구명을 넣는다.
            tool_result에는 MCP가 반환한 데이터를 가공하지 않고 JSON 객체 배열로 넣는다. 자연어 또는 Markdown으로 변환하지 않는다.
            answer에는 사용자의 요청에 대한 최종 답변을 자연스러운 한국어로 작성한다.
            answer를 작성할 때 tool_result의 내용을 이해하기 쉽게 요약하고 설명한다.
        """
    )
app = FastAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    tools = await mcp_toolset.list_tools()

    logger.info("MCP tool count=%d", len(tools))

    for tool in tools:
        logger.info(
            "MCP tool name=%s, description=%s",
            tool.name,
            tool.description,
        )

    yield

@app.post("/chat", response_model=AgentOutput)
async def chat(request: ChatRequest) -> AgentOutput:
    result = await agent.run(request.request)#사용자 요청과 MCP 도구 설명을 비교해 의도를 판단
    messages = result.all_messages()
    logger.info("Agent 실행 완료, message count=%d",len(messages),)
    log_mcp_execution(messages)
    tools = await mcp_toolset.list_tools()
    logger.info("MCP tools count=%d, names=%s", len(tools), [tool.name for tool in tools],)
    return result.output

