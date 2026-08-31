from typing import Any,Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)

class ChatResponse(BaseModel):
    result: Any

class AgentOutput(BaseModel):
    status: Literal[
        "ready",
        "missing_parameter",
        "not_found_tool"
    ]

    tool_name: str | None = None

    missing_parameter: list[str] = Field(
        default_factory=list
    )

    arguments: dict[str, Any] = Field(
        default_factory=dict
    )

    tool_result: dict[str, Any] | None = None