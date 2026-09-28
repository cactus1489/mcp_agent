import asyncio
import json
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic_ai import UsageLimits
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from .logging_utils import (
    log_mcp_server_connected,
    log_mcp_server_connection_failed,
    log_mcp_tool_complete,
    log_mcp_tool_error,
    log_mcp_tool_result,
    log_mcp_tool_start,
)
# 사용량 제한
AGENT_USAGE_LIMITS = UsageLimits(
    request_limit=15,
    tool_calls_limit=10,
    total_tokens_limit=500_000,
)
_OUTPUT_VALIDATORS: dict[tuple[str, str], Draft202012Validator] = {}


@dataclass(frozen=True)
class MCPServerConfig:
    id: str
    url: str
    prefix: str


@dataclass(frozen=True)
class MCPServerRuntime:
    config: MCPServerConfig
    toolset: MCPToolset
    prefixed_toolset: AbstractToolset[Any]


def parse_mcp_server_configs(raw: str) -> list[MCPServerConfig]:
    """Parse and validate the configured MCP HTTP servers."""
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP_SERVERS must be valid JSON.") from exc

    if not isinstance(values, list) or not values:
        raise ValueError("MCP_SERVERS must be a non-empty JSON array.")

    configs: list[MCPServerConfig] = []
    ids: set[str] = set()
    prefixes: set[str] = set()
    required_fields = {"id", "url", "prefix"}

    for value in values:
        if not isinstance(value, dict) or set(value) != required_fields:
            raise ValueError("Each MCP server must define id, url, and prefix.")

        server_id = value["id"]
        url = value["url"]
        prefix = value["prefix"]
        if not all(isinstance(item, str) and item.strip() for item in (server_id, url, prefix)):
            raise ValueError("MCP server id, url, and prefix must be non-empty strings.")

        server_id = server_id.strip()
        url = url.strip()
        prefix = prefix.strip()
        parsed_url = urlsplit(url)
        try:
            _ = parsed_url.port
        except ValueError as exc:
            raise ValueError("MCP server URL contains an invalid port.") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("MCP server URL must be an absolute HTTP(S) URL.")

        if server_id in ids:
            raise ValueError("MCP server IDs must be unique.")
        if prefix in prefixes:
            raise ValueError("MCP server prefixes must be unique.")
        ids.add(server_id)
        prefixes.add(prefix)
        configs.append(MCPServerConfig(server_id, url, prefix))

    return configs


def load_mcp_server_configs() -> list[MCPServerConfig]:
    """Load MCP server definitions from the environment."""
    return parse_mcp_server_configs(os.environ["MCP_SERVERS"])


def create_mcp_server_runtimes(
    configs: Iterable[MCPServerConfig],
    api_key: str,
) -> list[MCPServerRuntime]:
    """Create one raw and prefixed MCP toolset for each server."""
    runtimes: list[MCPServerRuntime] = []
    for config in configs:
        toolset = MCPToolset(
            config.url,
            id=config.id,
            headers={"Authorization": api_key},
            max_retries=1,
            tool_error_behavior="retry",
            init_timeout=10,
            read_timeout=30,
            process_tool_call=partial(process_mcp_tool_call, config.id),
        )
        runtimes.append(
            MCPServerRuntime(
                config=config,
                toolset=toolset,
                prefixed_toolset=toolset.prefixed(config.prefix),
            )
        )
    return runtimes


async def discover_mcp_servers(
    runtimes: Sequence[MCPServerRuntime],
) -> list[MCPServerRuntime]:
    """Return healthy MCP runtimes after concurrent tool discovery."""
    outcomes = await asyncio.gather(
        *(runtime.toolset.list_tools() for runtime in runtimes),
        return_exceptions=True,
    )
    healthy: list[MCPServerRuntime] = []

    for runtime, outcome in zip(runtimes, outcomes, strict=True):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome
            log_mcp_server_connection_failed(
                runtime.config.id,
                runtime.config.url,
                outcome,
            )
            continue

        tools = list(outcome)
        register_mcp_output_schemas(runtime.config.id, tools)
        log_mcp_server_connected(runtime.config.id, runtime.config.url, len(tools))
        healthy.append(runtime)

    if not healthy:
        raise RuntimeError("No MCP servers connected.")
    return healthy


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

# Tool별 Output Schema 저장
def register_mcp_output_schemas(server_id: str, tools: Iterable[Any]) -> None:
    """MCP Tool 이름별 outputSchema Validator를 등록한다."""
    for key in [key for key in _OUTPUT_VALIDATORS if key[0] == server_id]:
        del _OUTPUT_VALIDATORS[key]
    for tool in tools:
        schema = getattr(tool, "outputSchema", None)
        if schema:
            Draft202012Validator.check_schema(schema)
            _OUTPUT_VALIDATORS[(server_id, tool.name)] = Draft202012Validator(schema)

# MCP 공통 결과 검증
def validate_mcp_result(
    server_id: str,
    tool_name: str,
    result: Any,
) -> dict[str, Any]:
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
# Tool별 Output Validation
    validator = _OUTPUT_VALIDATORS.get((server_id, tool_name))
    if validator is not None:
        try:
            validator.validate(result)
        except ValidationError as exc:
            field = ".".join(str(item) for item in exc.absolute_path) or "root"
            raise MCPResultValidationError(
                f"MCP tool {tool_name!r} returned an invalid field: {field}."
            ) from exc

    return result
# MCP Tool 호출 하네스
async def process_mcp_tool_call(
    server_id: str,
    ctx,
    call_tool,
    tool_name: str,
    args: dict[str, Any],
):
    """MCP Tool을 한 번 호출하고 결과 검증 및 실행 Trace를 처리한다."""
    execution_trace_id = ctx.run_id or "unknown"
    tool_call_id = ctx.tool_call_id or "unknown"
    attempt = ctx.retry + 1
    started_at = time.perf_counter()

    log_mcp_tool_start(
        execution_trace_id,
        server_id,
        tool_name,
        tool_call_id,
        attempt,
        args,
    )
    try:
        result = await call_tool(tool_name, args)
        result = validate_mcp_result(server_id, tool_name, result)
        log_mcp_tool_result(
            execution_trace_id,
            server_id,
            tool_name,
            tool_call_id,
            result,
        )
    except Exception as exc:
        log_mcp_tool_error(
            execution_trace_id,
            server_id,
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
        server_id,
        tool_name,
        tool_call_id,
        attempt,
        time.perf_counter() - started_at,
    )
    return result
