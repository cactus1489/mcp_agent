import os
import time
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic_ai import UsageLimits
from pydantic_ai.mcp import MCPToolset

from .logging_utils import (
    log_mcp_tool_complete,
    log_mcp_tool_error,
    log_mcp_tool_result,
    log_mcp_tool_start,
)

AGENT_USAGE_LIMITS = UsageLimits(
    request_limit=15,
    tool_calls_limit=10,
    total_tokens_limit=500_000,
)
_OUTPUT_VALIDATORS: dict[str, Draft202012Validator] = {}


class MCPResultValidationError(ValueError):
    """MCP Tool 응답이 공통 결과 형식에 맞지 않을 때 발생하는 오류."""


class MCPToolResultError(RuntimeError):
    """MCP Tool이 실패 상태를 명시적으로 반환했을 때 발생하는 오류."""

    def __init__(self, tool_name: str, code: Any, message: str):
        """실패한 Tool 이름, 오류 코드 및 메시지로 오류를 생성한다."""
        self.code = code
        super().__init__(
            f"MCP tool {tool_name!r} failed with code={code!r}: {message}"
        )


def register_mcp_output_schemas(tools: Iterable[Any]) -> None:
    """MCP Tool 이름별 outputSchema Validator를 등록한다."""
    _OUTPUT_VALIDATORS.clear()
    for tool in tools:
        schema = getattr(tool, "outputSchema", None)
        if schema:
            Draft202012Validator.check_schema(schema)
            _OUTPUT_VALIDATORS[tool.name] = Draft202012Validator(schema)


def validate_mcp_result(tool_name: str, result: Any) -> dict[str, Any]:
    """MCP Tool 결과의 상태, 코드, 데이터 및 출처 형식을 검증한다."""
    if not isinstance(result, dict):
        raise MCPResultValidationError(
            f"MCP tool {tool_name!r} returned a non-object result."
        )

    status = result.get("status")
    code = result.get("code")

    if status != "success":
        message = result.get("msg") or "MCP tool execution failed."
        raise MCPToolResultError(tool_name, code, message)

    if str(code) != "200":
        raise MCPResultValidationError(
            f"MCP tool {tool_name!r} returned an invalid success code: {code!r}"
        )

    validator = _OUTPUT_VALIDATORS.get(tool_name)
    if validator is not None:
        try:
            validator.validate(result)
        except ValidationError as exc:
            field = ".".join(str(item) for item in exc.absolute_path) or "root"
            raise MCPResultValidationError(
                f"MCP tool {tool_name!r} returned an invalid field: {field}."
            ) from exc

    return result


async def process_mcp_tool_call(ctx, call_tool, tool_name: str, args: dict[str, Any]):
    """MCP Tool을 한 번 호출하고 결과 검증 및 실행 Trace를 처리한다."""
    execution_trace_id = ctx.run_id or "unknown"
    tool_call_id = ctx.tool_call_id or "unknown"
    attempt = ctx.retry + 1
    started_at = time.perf_counter()

    log_mcp_tool_start(
        execution_trace_id,
        tool_name,
        tool_call_id,
        attempt,
        args,
    )
    try:
        result = await call_tool(tool_name, args)
        result = validate_mcp_result(tool_name, result)
        log_mcp_tool_result(
            execution_trace_id,
            tool_name,
            tool_call_id,
            result,
        )
    except Exception as exc:
        log_mcp_tool_error(
            execution_trace_id,
            tool_name,
            tool_call_id,
            attempt,
            time.perf_counter() - started_at,
            exc,
            False,
        )
        raise

    log_mcp_tool_complete(
        execution_trace_id,
        tool_name,
        tool_call_id,
        attempt,
        time.perf_counter() - started_at,
    )
    return result


def create_mcp_toolset() -> MCPToolset:
    """연결 정보와 실행 정책이 적용된 MCP Toolset을 생성한다."""
    return MCPToolset(
        os.environ["MCP_SERVER_URL"],
        headers={"Authorization": os.environ["MCP_API_KEY"]},
        max_retries=1,
        tool_error_behavior="retry",
        init_timeout=10,
        read_timeout=30,
        process_tool_call=process_mcp_tool_call,
    )
