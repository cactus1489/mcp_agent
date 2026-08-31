from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

ParameterName = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$",
        description="MCP input schema의 정확한 property key",
    ),
]

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
    missing_parameter: list[ParameterName] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_result: (
    list[dict[str, Any]]
    | dict[str, list[dict[str, Any]]]
    | None
) = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "ready":
            if not self.tool_name:
                raise ValueError(
                    "ready 상태에는 실제 MCP tool_name이 필요합니다."
                )

            if self.tool_result is None:
                raise ValueError(
                    "ready 상태에는 tool_result가 필요합니다."
                )

        if (
            self.status == "missing_parameter"
            and not self.missing_parameter
        ):
            raise ValueError(
                "missing_parameter 상태에는 누락된 property key가 필요합니다."
            )

        return self