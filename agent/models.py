from typing import Any, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator

# API 입력 검증을 위해 필요
class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)

class AgentLLMOutput(BaseModel):
    status: Literal[
        "SUCCESS",
        "NEED_MORE_INFO",
        "NOT_APPLICABLE",
        "FAILED",
    ]
    missing_parameter: list[str] = Field(
        default_factory=list,
        description="MCP input schema에서 누락된 정확한 property key 목록",
    )
    answer: str = Field(
        description=(
            "사용자 질문과 MCP 실행 결과를 바탕으로 작성한 자연어 답변. "
            "MCP 결과에 주의사항이 있으면 답변에 포함한다. "
            "MCP 결과에 없는 전문적 주의사항은 추측하지 않는다."
        ),
    )

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "NEED_MORE_INFO" and not self.missing_parameter:
            raise ValueError("NEED_MORE_INFO 상태에는 누락된 key가 필요합니다.")
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
    missing_parameter: list[str] = Field(
        default_factory=list,
        description="MCP input schema에서 누락된 정확한 property key 목록",
    )
    answer: str

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "SUCCESS":
            if not self.tool_results:
                raise ValueError("SUCCESS 상태에는 tool_results가 필요합니다.")
        elif self.status == "NEED_MORE_INFO":
            if not self.missing_parameter:
                raise ValueError("NEED_MORE_INFO 상태에는 누락된 key가 필요합니다.")
        return self
