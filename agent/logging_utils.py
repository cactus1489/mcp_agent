import logging
import json
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("uvicorn.error")
_QA_LOG_LOCK = threading.Lock()
SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "client_secret",
    "phone",
    "phone_number",
    "address",
}
MASKED_VALUE = "***"


def _format_mcp_host(url: str) -> str:
    parsed = urlsplit(url)
    hostname = parsed.hostname or "unknown"
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{hostname}:{parsed.port}" if parsed.port is not None else hostname


def log_mcp_server_connected(server_id: str, url: str, tool_count: int) -> None:
    logger.info(
        "MCP SERVER CONNECTED id=%s host=%s tool_count=%d",
        server_id,
        _format_mcp_host(url),
        tool_count,
    )


def log_mcp_server_connection_failed(
    server_id: str,
    url: str,
    exc: BaseException,
) -> None:
    logger.error(
        "MCP SERVER CONNECTION FAILED id=%s host=%s error_type=%s",
        server_id,
        _format_mcp_host(url),
        type(exc).__name__,
    )

# 민감정보 마스킹
def mask_sensitive(value: Any) -> Any:
    """Return a copy with values of sensitive keys masked recursively."""
    if isinstance(value, Mapping):
        masked: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in SENSITIVE_KEYS:
                masked[key] = MASKED_VALUE
            else:
                masked[key] = mask_sensitive(item)
        return masked

    if isinstance(value, (list, tuple)):
        return [mask_sensitive(item) for item in value]

    return value

# Tool 호출 시작 로그
def log_mcp_tool_start(
    execution_trace_id: str,
    server_id: str,
    tool_name: str,
    tool_call_id: str,
    attempt: int,
    args: dict[str, Any],
) -> None:
    masked_args = json.dumps(
        mask_sensitive(args),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    logger.info(
        "[%s] MCP TOOL CALL START server=%s tool=%s call_id=%s attempt=%d args=%s",
        execution_trace_id,
        server_id,
        tool_name,
        tool_call_id,
        attempt,
        masked_args,
    )

# Tool 결과 요약 로그
def log_mcp_tool_result(
    execution_trace_id: str,
    server_id: str,
    tool_name: str,
    tool_call_id: str,
    result: Mapping[str, Any],
) -> None:
    """Log validated MCP result metadata without recording raw result values."""
    data = result.get("data")
    result_type = data.get("result_type") if isinstance(data, Mapping) else None
    items = data.get("items") if isinstance(data, Mapping) else None
    item_count = len(items) if isinstance(items, list) else None

    logger.info(
        "[%s] MCP TOOL RESULT server=%s tool=%s call_id=%s status=%s code=%s "
        "data_type=%s result_type=%s item_count=%s",
        execution_trace_id,
        server_id,
        tool_name,
        tool_call_id,
        result.get("status"),
        result.get("code"),
        type(data).__name__,
        result_type,
        item_count,
    )

# Tool 완료 로그
def log_mcp_tool_complete(
    execution_trace_id: str,
    server_id: str,
    tool_name: str,
    tool_call_id: str,
    attempt: int,
    duration: float,
) -> None:
    logger.info(
        "[%s] MCP TOOL CALL COMPLETE server=%s tool=%s call_id=%s attempt=%d duration=%.3fs",
        execution_trace_id,
        server_id,
        tool_name,
        tool_call_id,
        attempt,
        duration,
    )

# Tool 오류 로그
def log_mcp_tool_error(
    execution_trace_id: str,
    server_id: str,
    tool_name: str,
    tool_call_id: str,
    attempt: int,
    duration: float,
    exc: Exception,
    retrying: bool,
) -> None:
    event = "RETRY" if retrying else "FAILED"
    log = logger.warning if retrying else logger.error
    log(
        "[%s] MCP TOOL CALL %s server=%s tool=%s call_id=%s attempt=%d duration=%.3fs error_type=%s code=%s",
        execution_trace_id,
        event,
        server_id,
        tool_name,
        tool_call_id,
        attempt,
        duration,
        type(exc).__name__,
        getattr(exc, "code", None),
    )
# 최종 Agent 판단 로그
def log_agent_decision(
    execution_trace_id: str,
    status: str,
    tool_count: int,
    missing_parameter_count: int,
) -> None:
    """Log the final observable Agent decision without prompt or answer text."""
    logger.info(
        "[%s] AGENT DECISION status=%s tool_count=%d missing_parameter_count=%d",
        execution_trace_id,
        status,
        tool_count,
        missing_parameter_count,
    )


def log_chat_timeout(execution_trace_id: str, elapsed: float) -> None:
    logger.warning("[%s] TIMEOUT elapsed=%.3fs", execution_trace_id, elapsed)


def log_chat_complete(execution_trace_id: str, elapsed: float) -> None:
    logger.info(
        "[%s] CHAT COMPLETE elapsed=%.3fs",
        execution_trace_id,
        elapsed,
    )


def log_chat_failure(
    execution_trace_id: str,
    elapsed: float,
    exc: Exception,
) -> None:
    logger.error(
        "[%s] FAILED elapsed=%.3fs type=%s",
        execution_trace_id,
        elapsed,
        type(exc).__name__,
    )


# 질문·답변 감사 로그
def write_qa_audit_log(execution_trace_id: str, question: str, answer: str) -> None:
    """Append only the user's question and final answer to today's log file."""
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir / f"{today}.log"
    record = {
        "execution_trace_id": execution_trace_id,
        "question": question,
        "answer": answer,
    }

    with _QA_LOG_LOCK:
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
