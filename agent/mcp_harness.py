import asyncio
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_ai import UsageLimits
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from .logging_utils import (
    log_mcp_server_connected,
    log_mcp_server_connection_failed,
    log_mcp_tool_complete,
    log_mcp_tool_start,
)
AGENT_USAGE_LIMITS = UsageLimits(
     request_limit=15,
     tool_calls_limit=10,
     total_tokens_limit=500_000,
 )

_OUTPUT_VALIDATORS: dict[tuple[str, str], Draft202012Validator] = {}
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1),]

class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyString
    url: AnyHttpUrl
    prefix: NonEmptyString

    @field_validator("url", mode="before")
    @classmethod
    def require_explicit_host(cls, value):
        if isinstance(value, str) and not urlsplit(value.strip()).netloc:
            raise ValueError("MCP server URL must include an explicit host.")
        return value

class MCPServerConfigs(RootModel[list[MCPServerConfig]]):
    @model_validator(mode="after")
    def validate_servers(self):
        if not self.root:
            raise ValueError("At least one MCP server is required.")

        ids = [server.id for server in self.root]
        prefixes = [server.prefix for server in self.root]
        if len(ids) != len(set(ids)):
            raise ValueError("MCP server IDs must be unique.")
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("MCP server prefixes must be unique.")
        return self

@dataclass(frozen=True)
class MCPServerRuntime:
    config: MCPServerConfig
    toolset: MCPToolset
    prefixed_toolset: AbstractToolset[Any]

def parse_mcp_server_configs(raw: str) -> list[MCPServerConfig]:
    return MCPServerConfigs.model_validate_json(raw).root

def load_mcp_server_configs() -> list[MCPServerConfig]:
    return parse_mcp_server_configs(os.environ["MCP_SERVERS"])

async def discover_mcp_servers(
    configs: Iterable[MCPServerConfig],
    api_key: str,
) -> list[MCPServerRuntime]:
    runtimes: list[MCPServerRuntime] = []
    for config in configs:
        url = str(config.url)
        toolset = MCPToolset(
            url,
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
    outcomes = await asyncio.gather(*(runtime.toolset.list_tools() for runtime in runtimes), return_exceptions=True,)
    healthy: list[MCPServerRuntime] = []

    for runtime, outcome in zip(runtimes, outcomes, strict=True):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, Exception):
            log_mcp_server_connection_failed(runtime.config.id, str(runtime.config.url), outcome,)
            continue
        if isinstance(outcome, BaseException):
            raise outcome

        tools = list(outcome)
        register_mcp_output_schemas(runtime.config.id, tools)
        log_mcp_server_connected(runtime.config.id, str(runtime.config.url), len(tools),)
        healthy.append(runtime)

    if not healthy:
        raise RuntimeError("No MCP servers connected.")
    return healthy

class MCPResultValidationError(ValueError):
    """MCP Tool 응답이 공통 결과 형식에 맞지 않을 때 발생하는 오류."""

# Tool별 Output Schema 저장
def register_mcp_output_schemas(server_id: str, tools: Iterable[Any]) -> None:
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
    if not isinstance(result, dict):
        raise MCPResultValidationError(
            f"MCP tool {tool_name!r} returned a non-object result."
        )

    status = result.get("status")
    code = result.get("code")

    if status != "success" or str(code) != "200":
          raise MCPResultValidationError(
              f"MCP tool {tool_name!r} returned a failed result."
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
    execution_trace_id = ctx.run_id or "unknown"
    tool_call_id = ctx.tool_call_id or "unknown"
    attempt = ctx.retry + 1
    started_at = time.perf_counter()

    log_mcp_tool_start(execution_trace_id, server_id, tool_name, tool_call_id, attempt, args,)
    try:
        result = validate_mcp_result(server_id, tool_name, await call_tool(tool_name, args))
    except Exception as exc:
        exc.mcp_server_id = server_id
        exc.mcp_tool_name = tool_name
        raise
    log_mcp_tool_complete(execution_trace_id, server_id, tool_name, tool_call_id, attempt, time.perf_counter() - started_at,)
    return result