from collections.abc import Iterable
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from .models import ToolResult

OUTPUT_TOOL_NAME = "return_agent_output"

# MCP 실행 결과와 출처를 API 응답 형식으로 변환한다.
def get_mcp_results(messages: Iterable[ModelMessage]) -> list[ToolResult]:
    results: list[ToolResult] = []

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name != OUTPUT_TOOL_NAME:
                content = part.content
                if not isinstance(content, dict):
                    continue
                if (
                    content.get("status") != "success"
                    or str(content.get("code", "")) != "200"
                ):
                    continue
                data = content.get("data")
                source = content.get("source")

                results.append(
                    ToolResult(
                        tool_name=part.tool_name,
                        tool_call_id=part.tool_call_id,
                        data=data,
                        source=source,
                    )
                )

    return results
