from typing import Any, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator

# API 입력 검증을 위해 필요
class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)


class MissingInputTool(BaseModel):
    tool_name: str = Field(min_length=1, description="필수 입력이 부족한 실제 MCP 도구 이름")
    missing_parameter: list[str] = Field(
        min_length=1,
        description="누락된 필수 인자의 schema property key 목록",
    )

# 출력 상태 Schema 
class AgentLLMOutput(BaseModel):
    status: Literal[
        "SUCCESS",
        "NEED_MORE_INFO",
        "NOT_APPLICABLE",
        "FAILED",
    ] = Field(
        description="현재 요청의 최종 처리 상태",
    )
    missing_input_tools: list[MissingInputTool] = Field(
        default_factory=list,
        description="필수 입력값이 부족해 실행하지 못한 MCP 도구 목록",
    )
    # 변경 전 answer 설명: 복원을 위해 주석으로 보존한다.
    # answer: str = Field(
    #     description=(
    #         "사용자 질문과 MCP 실행 결과를 바탕으로 작성한 자연어 답변. "
    #         "MCP 결과에 주의사항이 있으면 답변에 포함한다. "
    #         "MCP 결과에 없는 전문적 주의사항은 추측하지 않는다. "
    #         "NOT_APPLICABLE이면 '요청을 처리할 수 있는 MCP 도구가 없습니다.'만 반환한다."
    #     ),
    # )
    answer: str = Field(
        description="사용자에게 표시할 최종 답변",
    )

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "NEED_MORE_INFO" and not self.missing_input_tools:
            raise ValueError("NEED_MORE_INFO 상태에는 missing_input_tools가 필요합니다.")
        return self

# MCP 결과 반환 형식을 보장하기 위해 필요
class ToolResult(BaseModel):
    tool_name: str
    tool_call_id: str
    data: Any = None
    source: Any = None


class AgentOutput(BaseModel):
    execution_trace_id: str
    status: Literal[
        "SUCCESS",
        "NEED_MORE_INFO",
        "NOT_APPLICABLE",
        "FAILED",
    ]
    tool_results: list[ToolResult] = Field(default_factory=list)
    missing_input_tools: list[MissingInputTool] = Field(
        default_factory=list,
        description="필수 입력값이 부족해 실행하지 못한 MCP 도구 목록",
    )
    answer: str
# 상태별 정합성 검증 
    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "SUCCESS":
            if not self.tool_results:
                raise ValueError("SUCCESS 상태에는 tool_results가 필요합니다.")
        elif self.status == "NEED_MORE_INFO":
            if not self.missing_input_tools:
                raise ValueError("NEED_MORE_INFO 상태에는 missing_input_tools가 필요합니다.")
        return self
