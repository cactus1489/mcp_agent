import secrets
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings
from .models import AgentLLMOutput, AgentOutput, ChatRequest
from .mcp_harness import (
    AGENT_USAGE_LIMITS,
    discover_mcp_servers,
    parse_mcp_server_configs,
)
from .mcp_result_utils import get_mcp_results
# 출처·시점은 response_templates 지침에 따라 LLM이 작성한다.
# from .provenance import collect_api_calls, inject_provenance
from .response_templates import AGENT_INSTRUCTIONS
from .web_ui import register_chat_ui
from .logging_utils import (
    log_chat_complete,
    write_qa_audit_log,
)
import asyncio

AGENT_API_KEY = settings.agent_api_key

#key값 받아서 검사
def verify_agent_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Require the configured authorization header before allowing requests."""
    if authorization is None or not secrets.compare_digest(
        authorization, AGENT_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

model = OpenAIChatModel(
    settings.llm_model,
    provider=OpenAIProvider(base_url=settings.llm_base_url),
    # provider=OpenAIProvider(api_key=settings.llm_key),
)

# LLM 모델, MCP Toolset, 구조화 출력 연결
def create_agent(toolsets: Sequence[Any]) -> Agent:
    agent = Agent(
      model,
      toolsets=list(toolsets),
    #   구조화된 최종 출력
      output_type=ToolOutput(
          AgentLLMOutput,
          name="return_agent_output",
          description=(
              "모든 상태의 최종 결과 반환용 도구다. 반드시 이 도구로 응답한다. "
              "적합한 MCP 도구가 없는 요청(조회 없는 인사·정체성 질문 포함)은 "
              "MCP 호출 없이 바로 NOT_APPLICABLE로 반환한다. "
              "SUCCESS는 실제 MCP 도구의 정상 처리 결과가 있을 때만 사용한다."
          )),
      retries={"output": 3},
      model_settings={"temperature": 0},
      instructions=AGENT_INSTRUCTIONS,
    )

    @agent.output_validator
    def validate_output(
        ctx: RunContext,
        output: AgentLLMOutput,
    ) -> AgentLLMOutput:
        if output.status == "SUCCESS" and not get_mcp_results(ctx.messages):
            raise ModelRetry(
                "정상 처리된 MCP 도구 결과가 없어 SUCCESS를 사용할 수 없습니다. "
                "적합한 도구가 없는 요청은 NOT_APPLICABLE로 반환하고, "
                "answer는 '요청을 처리할 수 있는 MCP 도구가 없습니다.'로 작성하세요. "
                "필수 입력이 부족하면 NEED_MORE_INFO, "
                "실제 실행 오류가 발생했다면 FAILED로 반환하세요."
            )
        return output

    return agent

# 서버 시작 시 Tool Schema 등록
@asynccontextmanager
async def lifespan(app: FastAPI):
    configs = parse_mcp_server_configs()
    healthy_runtimes = await discover_mcp_servers(configs, settings.mcp_api_key,)
    agent = create_agent([runtime.prefixed_toolset for runtime in healthy_runtimes])
    async with agent:
        app.state.agent = agent
        try:
            yield
        finally:
            del app.state.agent

app = FastAPI(lifespan=lifespan)
register_chat_ui(app, authorization=AGENT_API_KEY)

def get_agent(request: Request) -> Agent:
    return request.app.state.agent

@app.post("/chat", response_model=AgentOutput, dependencies=[Depends(verify_agent_api_key)],)
async def chat(request: ChatRequest, runtime_agent: Agent = Depends(get_agent),) -> AgentOutput:
    execution_trace_id = str(uuid4())
    started_at = time.perf_counter()
    error: Exception | None = None
# 전체 요청 Timeout
    try:
        # with collect_api_calls() as api_calls:
        async with asyncio.timeout(180):
            result = await runtime_agent.run(
                request.request,
                run_id=execution_trace_id,
                usage_limits=AGENT_USAGE_LIMITS,
            )
# Tool 결과 응답 연결
        tool_results = get_mcp_results(result.all_messages())
        final_status = "SUCCESS" if tool_results else result.output.status
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status=final_status,
            missing_input_tools=result.output.missing_input_tools,
            tool_results=tool_results,
            # answer=inject_provenance(result.output.answer, api_calls),
            answer=result.output.answer,
        )
    except TimeoutError as exc:
        error = exc
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status="FAILED",
            answer="외부 시스템의 응답이 지연되어 조회를 완료하지 못했습니다.",)
    except Exception as exc:
        error = exc
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status="FAILED",
            answer="외부 전문정보 조회 중 오류가 발생했습니다.",
        )

    write_qa_audit_log(execution_trace_id, request.request, response.answer)
    missing_parameter_count = sum(
        len(tool.missing_parameter) for tool in response.missing_input_tools
    )
    log_chat_complete(execution_trace_id, response.status, len(response.tool_results), missing_parameter_count, time.perf_counter() - started_at, error,)
    return response
