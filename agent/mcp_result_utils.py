from collections.abc import Iterable
from typing import Any

from pydantic_ai.messages import ToolReturnPart

OUTPUT_TOOL_NAME = "return_agent_output"

# MCP 실행 결과와 출처를 API 응답 형식으로 변환한다.
def get_mcp_results(messages: Iterable) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name != OUTPUT_TOOL_NAME:
                content = part.content
                if isinstance(content, dict):
                    data = content.get("data")
                    source = content.get("source")
                else:
                    data = content
                    source = None

                results.append(
                    {
                        "tool_name": part.tool_name,
                        "tool_call_id": part.tool_call_id,
                        "data": data,
                        "source": source,
                    }
                )

    return results
