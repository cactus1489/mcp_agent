import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .models import AgentLLMOutput, AgentOutput, ChatRequest
from .mcp_harness import (
    AGENT_USAGE_LIMITS,
    create_mcp_toolset,
    register_mcp_output_schemas,
)
from .mcp_result_utils import get_mcp_results
from .logging_utils import (
    log_agent_decision,
    log_chat_complete,
    log_chat_failure,
    log_chat_timeout,
    write_qa_audit_log,
)
import asyncio

load_dotenv(Path(__file__).parent.parent / ".env")

AGENT_API_KEY = os.environ["AGENT_API_KEY"]
agent_bearer = HTTPBearer(auto_error=False)

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

mcp_toolset = create_mcp_toolset()

agent = Agent(
      model,
      toolsets=[mcp_toolset],
      output_type=ToolOutput(
          AgentLLMOutput,
          name="return_agent_output",
          description="최종 결과 반환용 도구다. 반드시 이 도구를 호출해 응답하며 일반 텍스트로 답하지 않는다."),
      retries={"output": 3},#잘못된 출력을 감지하고 정해진 횟수만큼 재시도하도록 관리(하네스)
      model_settings={"temperature": 0},
      #입력값 누락 여부 판단 및 추가 질문 유도
      instructions=
        """
            사용자 요청과 MCP 도구의 목적, 입력 조건, 출력 정보를 비교하여 가장 적절한 도구를 선택한다.
            MCP 도구가 필요하지 않거나 적합한 도구가 없으면 status="NOT_APPLICABLE",
            missing_parameter=[], answer="요청을 처리할 수 있는 MCP 도구가 없습니다."로 반환한다.
            적합한 도구가 있지만 필수 인자가 부족하면 도구를 호출하지 않고 status="NEED_MORE_INFO"로 반환한다.
            missing_parameter에는 누락된 input schema의 property key만 그대로 넣는다.
            사용자 입력이나 이전 도구 결과에 없는 인자 값은 추측, 생성 또는 임의 변환하지 않는다.
            상세조회 도구는 필요한 식별자가 확보된 경우에만 호출한다.
            검색 결과가 비어 있어도 조건을 임의로 변경하여 같은 도구를 다시 호출하지 않는다.
            도구 선택 시 질문 조건 일치도, 데이터 출처, 공식성, 최신성을 고려하며, 서로 다른 출처의 데이터를 동일하게 간주하지 않는다.
            도구 호출 및 결과 처리에 성공하면 status="SUCCESS"로 반환한다.
            도구 실행 또는 결과 검증에 실패하면 status="FAILED"로 반환한다.
            answer는 사용자 입력과 MCP 실행 결과만을 근거로 자연스럽고 완결된 한국어로 작성한다.
            MCP 결과를 이해하기 쉽게 요약하되 명칭, 수치, 단위, 날짜, 등급, 출처 등 원래 의미와 값을 변경하지 않는다.
            MCP 결과에 없는 사실, 해석, 평가 또는 주의사항을 추측하여 추가하지 않는다.
            MCP 결과에 warning, caution 또는 안전 관련 정보가 명시된 경우에만 answer 마지막에 "주의사항:"으로 구분하여 포함한다.
            Markdown만 사용하며 HTML 또는 XML 태그는 사용하지 않는다.
            반환 전 answer가 MCP 결과와 일치하고 모든 문장이 완결되었는지 확인한다.
            MCP 결과에 없는 일반적인 권고나 관행적인 안내 문구도 추가하지 않는다.
            사용자가 요청한 정보가 MCP 결과에 없으면 해당 정보가 제공되지 않았다고 명시한다.
            비교 결과를 설명할 때 확인한 범위를 넘어 "유일한", "모두", "완전히 동일한"과 같이 단정하지 않는다.
        """
    )

#서버 시작 전 툴 목록 가져오기
@asynccontextmanager
async def lifespan(app: FastAPI):
    tools = await mcp_toolset.list_tools()
    register_mcp_output_schemas(tools)
    yield

app = FastAPI(lifespan=lifespan)

@app.post(
    "/chat",
    response_model=AgentOutput,
    dependencies=[Depends(verify_agent_api_key)],
)
async def chat(request: ChatRequest) -> AgentOutput:
    execution_trace_id = str(uuid4())
    started_at = time.perf_counter()
    # log_chat_start(execution_trace_id, len(request.request), 180)

    try:
        async with asyncio.timeout(180):
            result = await agent.run(request.request, run_id=execution_trace_id, usage_limits=AGENT_USAGE_LIMITS,)

        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status=result.output.status,
            missing_parameter=result.output.missing_parameter,
            tool_results=get_mcp_results(result.all_messages()),
            answer=result.output.answer,
        )
    except TimeoutError:
        log_chat_timeout(execution_trace_id, time.perf_counter() - started_at,)
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status="FAILED",
            answer="외부 시스템의 응답이 지연되어 조회를 완료하지 못했습니다.",)
    except Exception as exc:
        log_chat_failure(execution_trace_id,time.perf_counter() - started_at, exc,)
        response = AgentOutput(
            execution_trace_id=execution_trace_id,
            status="FAILED",
            answer="외부 전문정보 조회 중 오류가 발생했습니다.",
        )

    log_agent_decision(
        execution_trace_id,
        response.status,
        len(response.tool_results),
        len(response.missing_parameter),
    )
    write_qa_audit_log(execution_trace_id, request.request, response.answer)
    log_chat_complete(execution_trace_id, time.perf_counter() - started_at)
    return response
