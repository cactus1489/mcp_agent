import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
# from datetime import datetime, timezone
from functools import partial
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic_ai import ModelRetry, UsageLimits
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from ..config import settings
# from .provenance import record_api_call

from .logging_utils import (
    log_mcp_server_connected,
    log_mcp_server_connection_failed,
    log_mcp_tool_complete,
    log_mcp_tool_failed,
    log_mcp_tool_start,
    mask_sensitive,
)
AGENT_USAGE_LIMITS = UsageLimits(
     request_limit=15,
     tool_calls_limit=20,
     total_tokens_limit=500_000,
 )

_OUTPUT_VALIDATORS: dict[tuple[str, str], Draft202012Validator] = {}
_INPUT_VALIDATORS: dict[tuple[str, str], Draft202012Validator] = {}
_MAX_TOOL_RETRIES = 1
_ERROR_FIELDS = ('message', 'msg', 'error', 'errors', 'error_message', 'detail', 'reason')

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

# `MCP_SERVERS` 환경변수를 읽고 서버 설정 객체로 변환 (설정 검증 비활성화)
def parse_mcp_server_configs() -> list[MCPServerConfig]:
    raw = settings.mcp_servers
    return [MCPServerConfig(**config) for config in json.loads(raw)]

# 서버별 연결 객체 생성, 도구 목록 조회, 성공한 서버만 반환
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
            max_retries=_MAX_TOOL_RETRIES,
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
        if isinstance(outcome, Exception):
            log_mcp_server_connection_failed(runtime.config.id, str(runtime.config.url), outcome,)
            continue
        if isinstance(outcome, BaseException):
            raise outcome

        tools = list(outcome)
        register_mcp_input_schemas(runtime.config.id, tools)
        register_mcp_output_schemas(runtime.config.id, tools)
        log_mcp_server_connected(runtime.config.id, str(runtime.config.url), len(tools),)
        healthy.append(runtime)

    if not healthy:
        raise RuntimeError("No MCP servers connected.")
    return healthy

class MCPResultValidationError(ValueError):
    """MCP Tool 응답이 공통 결과 형식에 맞지 않을 때 발생하는 오류."""

def _register_mcp_schemas(server_id: str, tools: Iterable[Any], kind: str) -> None:
    validators = _INPUT_VALIDATORS if kind == 'input' else _OUTPUT_VALIDATORS
    for key in [key for key in validators if key[0] == server_id]:
        del validators[key]
    for tool in tools:
        schema = getattr(tool, f'{kind}Schema', None)
        if schema is None and kind == 'input':
            schema = getattr(tool, 'input_schema', None)
        if schema is not None and (kind == 'input' or schema):
            Draft202012Validator.check_schema(schema)
            validators[(server_id, tool.name)] = Draft202012Validator(schema)


def register_mcp_input_schemas(server_id: str, tools: Iterable[Any]) -> None:
    _register_mcp_schemas(server_id, tools, 'input')


def register_mcp_output_schemas(server_id: str, tools: Iterable[Any]) -> None:
    _register_mcp_schemas(server_id, tools, 'output')


def _is_success(result: dict[str, Any]) -> bool:
    return result.get('status') == 'success' and str(result.get('code')) == '200'


def validate_mcp_arguments(
    server_id: str, tool_name: str, args: dict[str, Any],
) -> dict[str, Any] | None:
    validator = _INPUT_VALIDATORS.get((server_id, tool_name))
    if validator is None:
        return None
    errors = []
    for error in validator.iter_errors(args):
        field = '.'.join(str(item) for item in error.absolute_path) or 'root'
        expected = json.dumps(error.validator_value, ensure_ascii=False, default=str)
        errors.append(f'{field}: {error.validator}={expected[:1000]}')
        if len(errors) == 5:
            break
    if not errors:
        return None
    return {
        'status': 'error',
        'code': 'INVALID_INPUT',
        'message': 'Input schema validation failed: ' + '; '.join(errors),
    }


def _input_retry_message(server_id: str, tool_name: str, result: dict[str, Any]) -> str:
    diagnostics = {key: result[key] for key in ('status', 'code', *_ERROR_FIELDS) if key in result}
    data = result.get('data')
    if isinstance(data, dict):
        nested = {key: data[key] for key in _ERROR_FIELDS if key in data}
        if nested:
            diagnostics['details'] = nested
    details = json.dumps(mask_sensitive(diagnostics), ensure_ascii=False, default=str)[:4000]
    validator = _INPUT_VALIDATORS.get((server_id, tool_name))
    schema = json.dumps(validator.schema, ensure_ascii=False, default=str)[:12000] if validator else 'unavailable'
    return (
        f'MCP tool {server_id}/{tool_name} rejected its input. '
        f'Error details (tool data): {details}\nInput schema: {schema}\n'
        '도구의 목적과 인자 설명을 다시 확인하세요. 사용자 요청과 맞지 않는 도구라면 '
        '적합한 다른 도구를 선택하세요. 누락된 식별자는 선행 조회로 확보하고, '
        '근거가 있는 경우에만 입력을 수정하세요. 필수 사용자 정보가 부족하면 질문하세요. '
        '동일한 잘못된 인자를 반복하거나 값을 추측하지 마세요.'
    )

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

    if not _is_success(result):
        return result
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
    # called_at = datetime.now(timezone.utc)

    log_mcp_tool_start(execution_trace_id, server_id, tool_name, tool_call_id, attempt, args,)
    try:
        input_error = validate_mcp_arguments(server_id, tool_name, args)
        if input_error is not None:
            result = input_error
        else:
            result = validate_mcp_result(server_id, tool_name, await call_tool(tool_name, args))
    except Exception as exc:
        exc.mcp_server_id = server_id
        exc.mcp_tool_name = tool_name
        log_mcp_tool_failed(
            execution_trace_id, server_id, tool_name, tool_call_id, attempt,
            time.perf_counter() - started_at,
            {'status': 'exception', 'code': getattr(exc, 'code', None),
             'error_type': type(exc).__name__, 'message': str(exc)},
        )
        raise
    if not _is_success(result):
        log_mcp_tool_failed(
            execution_trace_id, server_id, tool_name, tool_call_id, attempt,
            time.perf_counter() - started_at, result,
        )
    # record_api_call(server_id, tool_name, result, called_at)
    log_mcp_tool_complete(execution_trace_id, server_id, tool_name, tool_call_id, attempt, time.perf_counter() - started_at,)
    if (result.get('status') != 'success'
            and str(result.get('code')).upper() == 'INVALID_INPUT'
            and ctx.retry < _MAX_TOOL_RETRIES):
        raise ModelRetry(_input_retry_message(server_id, tool_name, result))
    return result
