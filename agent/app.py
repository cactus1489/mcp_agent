import os
import secrets
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .models import AgentLLMOutput, AgentOutput, ChatRequest
from .mcp_harness import (
    AGENT_USAGE_LIMITS,
    discover_mcp_servers,
    load_mcp_server_configs,
)
from .mcp_result_utils import get_mcp_results
from .logging_utils import (
    log_chat_complete,
    write_qa_audit_log,
)
import asyncio

load_dotenv(Path(__file__).parent.parent / ".env")

AGENT_API_KEY = os.environ["AGENT_API_KEY"]
agent_bearer = HTTPBearer(auto_error=False)

#key값 받아서 검사
def verify_agent_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(agent_bearer),
) -> None:
    """Require a valid bearer token before allowing agent requests."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, AGENT_API_KEY)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

model = OpenAIChatModel(
    os.environ["LLM_MODEL"],
    provider=OpenAIProvider(base_url=os.environ["LLM_BASE_URL"]),
)

# LLM 모델, MCP Toolset, 구조화 출력 연결
def create_agent(toolsets: Sequence[Any]) -> Agent:
    return Agent(
      model,
      toolsets=list(toolsets),
    #   구조화된 최종 출력
      output_type=ToolOutput(
          AgentLLMOutput,
          name="return_agent_output",
          description="최종 결과 반환용 도구다. 반드시 이 도구를 호출해 응답하며 일반 텍스트로 답하지 않는다."),
      retries={"output": 3},
      model_settings={"temperature": 0},
    #   Tool 선택·상태 판단 정책
     instructions=
        """
            사용자 요청을 분석하여 필요한 조회 항목을 식별하고, 각 항목에 가장 적합한 MCP 도구를 선택한다.
            도구 선택 시 사용자 요청과 MCP 도구의 목적, input schema, 출력 정보를 비교한다.

            사용자 요청에 서로 독립된 조회 항목이 여러 개 있으면 각각 분리하고, 각 항목에 적합한 도구를 선택하여 모두 호출한다.
            서로 다른 전문 영역의 조회 항목을 하나의 도구로 임의 대체하지 않으며, 필요한 경우 서로 다른 MCP 서버의 도구를 각각 호출한다.
            필수 인자가 부족하다는 이유로 관련성이 낮은 다른 도구를 대신 선택하지 않는다.
            모든 조회 항목의 도구 호출이 완료된 후 결과를 통합하여 답변한다.
            도구 인자는 사용자 입력, 이전 도구 결과 또는 Schema에 명시된 변환 규칙에서 확인된 값만 사용한다. 
            Schema에 명시적인 변환 규칙이나 enum이 없으면 원문을 번역·코드화하거나 새로운 값을 생성하지 않는다.

            MCP 도구가 필요하지 않거나 적합한 도구가 없으면 status="NOT_APPLICABLE",
            missing_parameter=[], answer="요청을 처리할 수 있는 MCP 도구가 없습니다."로 반환한다.

            적합한 도구가 있지만 필수 인자가 부족하면 해당 도구를 호출하지 않고 status="NEED_MORE_INFO"로 반환한다.
            missing_parameter에는 누락된 input schema의 property key만 그대로 넣는다.

            상세조회 도구는 필요한 식별자가 확보된 경우에만 호출한다.
            검색 결과가 비어 있어도 조건을 임의로 변경하여 같은 도구를 다시 호출하지 않는다.

            도구 선택 시 질문 조건 일치도, 데이터 출처, 공식성, 최신성을 고려하며, 서로 다른 출처의 데이터를 동일하게 간주하지 않는다.

            필요한 모든 도구의 실행과 결과 처리가 성공한 경우에만 status="SUCCESS"로 반환한다.
            하나라도 도구 실행 또는 결과 검증에 실패하면 전체 status="FAILED"로 반환한다.

            answer는 사용자 입력과 MCP 실행 결과만을 근거로 자연스럽고 완결된 한국어로 작성한다.
            MCP 결과를 이해하기 쉽게 요약하되 명칭, 수치, 단위, 날짜, 등급, 출처 등 원래 의미와 값을 변경하지 않는다.
            MCP 결과에 없는 사실, 해석, 평가, 권고 또는 안내를 추측하여 추가하지 않는다.
            사용자가 요청한 정보가 MCP 결과에 없으면 해당 정보가 제공되지 않았다고 명시한다.

            MCP 결과에 warning, caution 또는 안전 관련 정보가 명시된 경우에만 answer 마지막에 "주의사항:"으로 구분하여 포함한다.
            비교 결과는 실제 확인한 범위 내에서만 설명하며, 근거 없이 "유일한", "모두", "완전히 동일한"과 같이 단정하지 않는다.

            Markdown만 사용하며 HTML 또는 XML 태그는 사용하지 않는다.
            반환 전 answer가 MCP 결과와 일치하고 모든 문장이 완결되었는지 확인한다.
        """
    )

# 서버 시작 시 Tool Schema 등록
@asynccontextmanager
async def lifespan(app: FastAPI):
    configs = load_mcp_server_configs()
    healthy_runtimes = await discover_mcp_servers(
        configs,
        os.environ["MCP_API_KEY"],
    )
    agent = create_agent(
        [runtime.prefixed_toolset for runtime in healthy_runtimes]
    )
    async with agent:
        app.state.agent = agent
        try:
            yield
        finally:
            del app.state.agent

app = FastAPI(lifespan=lifespan)

def get_agent(request: Request) -> Agent:
    return request.app.state.agent

@app.post("/chat", response_model=AgentOutput, dependencies=[Depends(verify_agent_api_key)],)
async def chat(request: ChatRequest, runtime_agent: Agent = Depends(get_agent),) -> AgentOutput:
    execution_trace_id = str(uuid4())
    started_at = time.perf_counter()
    error: Exception | None = None
# 전체 요청 Timeout
    try:
        async with asyncio.timeout(180):
            result = await runtime_agent.run(
                request.request,
                run_id=execution_trace_id,
                usage_limits=AGENT_USAGE_LIMITS,
            )
# Tool 결과 응답 연결
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status=result.output.status,
            missing_parameter=result.output.missing_parameter,
            tool_results=get_mcp_results(result.all_messages()),
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
    log_chat_complete(execution_trace_id, response.status, len(response.tool_results), len(response.missing_parameter), time.perf_counter() - started_at, error,)
    return response
