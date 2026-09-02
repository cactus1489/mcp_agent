from typing import Any, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)

class AgentOutput(BaseModel):
    status: Literal[
        "ready",
        "missing_parameter",
        "not_found_tool",
    ]
    tool_name: str | None = None
    missing_parameter: list[str] = Field(
        default_factory=list,
        description="MCP input schema에서 누락된 정확한 property key 목록",)
    tool_result: list[dict[str, Any]] | None = None
    answer: str = Field(
        description=(
            "사용자 질문과 MCP 실행 결과를 바탕으로 작성한 자연어 답변. "
            "MCP 결과에 없는 정보는 추측하지 않는다."
        ),
    )
    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "ready":
            if not self.tool_name or self.tool_result is None:
                raise ValueError(
                    "ready 상태에는 tool_name과 tool_result가 필요합니다."
                )

        elif self.status == "missing_parameter":
            if not self.tool_name or not self.missing_parameter:
                raise ValueError(
                    "missing_parameter 상태에는 tool_name과 "
                    "missing_parameter가 필요합니다."
                )

        return self