import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from models import AgentOutput, ChatRequest

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
          description="에이전트의 최종 실행 상태를 구조화하여 반환한다.",
      ),
      model_settings={"temperature": 0}, #0: 가장 일관적이고 예측 가능한 응답
      retries={"output": 3},
        instructions="""
            사용자 요청과 각 MCP 도구의 설명을 비교하여 적절한 도구를 선택한다.

            도구 인자는 사용자 입력이나 이전 도구 결과에서 확인된 값만 사용한다.
            특히 코드, ID, 일련번호는 절대 추측하거나 생성하지 않는다.

            선택한 도구의 required 인자가 부족하면 도구를 호출하지 않고
            status="missing_parameter"로 반환하며, 누락된 property key를
            MCP input schema에 정의된 이름 그대로 넣는다.

            적절한 도구가 없으면 status="not_found_tool",
            도구 실행에 성공하면 status="ready"로 반환한다.

            tool_name과 arguments에는 실제 호출한 도구명과 인자를 넣고,
            tool_result에는 도구가 반환한 JSON 객체 배열을 그대로 넣는다.

            반드시 return_agent_output을 호출하며 일반 텍스트는 출력하지 않는다.
            tool_result는 자연어 문자열이 아니라 MCP 결과의 각 컬럼을 key로 갖는 JSON 객체 배열로 반환하며, result 필드에 자연어나 Markdown을 넣지 않는다.
  """
    )
app = FastAPI()

@app.post("/chat", response_model=AgentOutput)
async def chat(request: ChatRequest) -> AgentOutput:
    result = await agent.run(request.request)
    return result.output
