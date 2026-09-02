import logging
from collections.abc import Iterable

from pydantic_ai.messages import ToolCallPart, ToolReturnPart

logger = logging.getLogger("uvicorn.error")
OUTPUT_TOOL_NAME = "return_agent_output"


def log_mcp_execution(messages: Iterable) -> None:
    for message in messages:
        for part in message.parts:
            if getattr(part, "tool_name", None) == OUTPUT_TOOL_NAME:
                continue

            if isinstance(part, ToolCallPart):
                logger.info("MCP CALL %s %s", part.tool_name, part.args_as_dict(),)
            elif isinstance(part, ToolReturnPart):
                logger.info("MCP RESULT %s %s", part.tool_name, part.content,)