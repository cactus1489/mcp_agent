# MCP Agent Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the five remaining MCP Agent hardening deliverables: safe result and decision logs, policy-controlled retry/fallback, a real-response regression fixture, and final verification.

**Architecture:** Keep the existing `MCPToolset -> process_mcp_tool_call -> AgentOutput` flow. Move transient execution retry and fallback ownership into `mcp_harness.py`, configure PydanticAI to propagate MCP errors instead of independently asking the model to retry, and emit only metadata summaries to logs. Keep the production fallback allowlist empty until compatible tools are explicitly approved.

**Tech Stack:** Python 3.11+, FastAPI, PydanticAI, FastMCP, Pydantic, `jsonschema`, `httpx`, `unittest`

**Spec:** `docs/superpowers/plans/2026-09-22-mcp-agent-priority-hardening.md` and `C:\Users\user\Documents\카카오톡 받은 파일\AI_이삭이_MCP_Agent_Architecture_Specification.docx`

## Global Constraints

- Preserve the API statuses `SUCCESS`, `NEED_MORE_INFO`, `NOT_APPLICABLE`, and `FAILED`.
- Never log raw MCP `data`, `source`, user questions, answers, credentials, personal information, or exception messages in execution traces.
- Never retry input errors, MCP output validation errors, or non-retryable 4xx tool errors.
- Execute fallback only after retryable primary failures are exhausted and only for explicitly allowlisted tools with compatible input schemas.
- Keep the production `FALLBACK_TOOL_MAP` empty initially.
- Preserve MCP `data` and `source` values in the API response.
- Add a failing test before each implementation change.

## Review Focus

- A valid MCP result must produce a summary log without exposing raw result values.
- Every `/chat` outcome, including exceptions and timeouts, must produce one final Agent decision log.
- A transient primary failure must be retried exactly once; validation and 4xx errors must not be retried.
- Fallback metadata must expose the actual executed tool without leaking internal `_harness` data through the API.
- The real pesticide fixture must exercise schema registration, validation, safe trace, and API result extraction without network access.

---

### Task 1: Safe MCP Result and Agent Decision Logs

**Files:**
- Modify: `agent/logging_utils.py`
- Modify: `agent/mcp_harness.py`
- Modify: `agent/app.py`
- Test: `tests/test_logging_utils.py`
- Test: `tests/test_mcp_harness.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces: `log_mcp_tool_result(execution_trace_id: str, tool_name: str, tool_call_id: str, result: Mapping[str, Any]) -> None`
- Produces: `log_agent_decision(execution_trace_id: str, status: str, tool_count: int, missing_parameter_count: int) -> None`

- [x] **Step 1: Add failing result-summary and Agent-decision logging tests**

```python
def test_tool_result_log_contains_only_summary(self):
    result = {
        "status": "success",
        "code": "200",
        "source": "private source text",
        "data": {"result_type": "candidates", "items": [{"phone": "010-0000-0000"}]},
    }
    with self.assertLogs("uvicorn.error", level="INFO") as captured:
        log_mcp_tool_result("trace-1", "sample", "call-1", result)
    logs = "\n".join(captured.output)
    self.assertIn("result_type=candidates", logs)
    self.assertIn("item_count=1", logs)
    self.assertNotIn("private source text", logs)
    self.assertNotIn("010-0000-0000", logs)

def test_agent_decision_log_contains_counts_only(self):
    with self.assertLogs("uvicorn.error", level="INFO") as captured:
        log_agent_decision("trace-1", "SUCCESS", 2, 0)
    self.assertIn("status=SUCCESS tool_count=2 missing_parameter_count=0", "\n".join(captured.output))
```

- [x] **Step 2: Run the focused tests and confirm they fail because the functions do not exist**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Python\Python311\python.exe -m unittest tests.test_logging_utils -v
```

Expected: FAIL on missing `log_mcp_tool_result` and `log_agent_decision`.

- [x] **Step 3: Implement metadata-only log functions**

```python
def log_mcp_tool_result(execution_trace_id, tool_name, tool_call_id, result):
    data = result.get("data")
    result_type = data.get("result_type") if isinstance(data, Mapping) else None
    items = data.get("items") if isinstance(data, Mapping) else None
    item_count = len(items) if isinstance(items, list) else None
    logger.info(
        "[%s] MCP TOOL RESULT tool=%s call_id=%s status=%s code=%s data_type=%s result_type=%s item_count=%s",
        execution_trace_id, tool_name, tool_call_id,
        result.get("status"), result.get("code"), type(data).__name__, result_type, item_count,
    )

def log_agent_decision(execution_trace_id, status, tool_count, missing_parameter_count):
    logger.info(
        "[%s] AGENT DECISION status=%s tool_count=%d missing_parameter_count=%d",
        execution_trace_id, status, tool_count, missing_parameter_count,
    )
```

- [x] **Step 4: Connect the logs at the validated-result and final-response boundaries**

Call `log_mcp_tool_result()` immediately after `validate_mcp_result()`. Call `log_agent_decision()` once after the `/chat` try/except has produced `response` and before the QA audit log is written.

- [x] **Step 5: Run focused and full regression tests**

```powershell
C:\Python\Python311\python.exe -m unittest tests.test_logging_utils tests.test_mcp_harness tests.test_chat -v
```

Expected: PASS with no raw source, item content, question, answer, or exception message in captured execution logs.

---

### Task 2: Policy-Controlled Retry and Fallback

**Files:**
- Modify: `agent/mcp_harness.py`
- Modify: `agent/logging_utils.py`
- Modify: `agent/mcp_result_utils.py`
- Test: `tests/test_mcp_harness.py`
- Test: `tests/test_mcp_result_utils.py`

**Interfaces:**
- Produces: `FALLBACK_TOOL_MAP: dict[str, tuple[str, ...]] = {}`
- Produces: `is_retryable_error(exc: Exception) -> bool`
- Produces: `get_fallback_tools(tool_name: str) -> tuple[str, ...]`
- Produces: internal result metadata `_harness.requested_tool_name`, `_harness.executed_tool_name`, `_harness.fallback_used`

- [ ] **Step 1: Add failing retry-classification tests**

Cover built-in `TimeoutError`, `httpx.TimeoutException`, `httpx.TransportError`, and `MCPToolResultError` codes `408`, `429`, `500`, `502`, `503`, `504` as retryable. Cover `MCPResultValidationError` and Tool codes `400`, `401`, `403`, `404`, `422` as non-retryable.

- [ ] **Step 2: Add failing execution-order tests**

```python
async def test_retries_transient_primary_once_then_succeeds(self):
    call_tool = SequenceToolCall([TimeoutError("temporary"), SUCCESS_RESULT])
    result = await process_mcp_tool_call(TEST_CONTEXT, call_tool, "primary", {})
    self.assertEqual(call_tool.call_count, 2)
    self.assertEqual(result["_harness"]["executed_tool_name"], "primary")

async def test_falls_back_after_primary_retry_is_exhausted(self):
    calls = []
    async def call_tool(name, args):
        calls.append(name)
        if name == "primary":
            raise TimeoutError("temporary")
        return SUCCESS_RESULT
    with patch.dict(FALLBACK_TOOL_MAP, {"primary": ("backup",)}, clear=True):
        result = await process_mcp_tool_call(TEST_CONTEXT, call_tool, "primary", {})
    self.assertEqual(calls, ["primary", "primary", "backup"])
    self.assertEqual(result["_harness"]["executed_tool_name"], "backup")
```

Also assert that validation errors and 4xx errors perform one call, an empty allowlist propagates the final primary error, and fallback failure propagates the last error.

- [ ] **Step 3: Implement retry ownership inside the harness**

Create `_call_tool_with_retry()` with two total attempts for retryable failures and one attempt for non-retryable failures. Log intermediate retryable failures with `retrying=True`; log `FAILED` only after all approved fallback options are exhausted.

- [ ] **Step 4: Configure PydanticAI not to create a second independent retry loop**

Set `tool_error_behavior="error"` so the callback receives underlying MCP exceptions and owns retry/fallback policy. Set `max_retries=0` for MCP Tool model retries; argument-schema correction remains covered by the Agent output retry and explicit `NEED_MORE_INFO` behavior.

- [ ] **Step 5: Add empty production allowlist, fallback execution, and metadata**

```python
FALLBACK_TOOL_MAP: dict[str, tuple[str, ...]] = {}

def get_fallback_tools(tool_name: str) -> tuple[str, ...]:
    return FALLBACK_TOOL_MAP.get(tool_name, ())
```

Copy validated results before adding `_harness`; never mutate the server-owned object. Pass unchanged args only to allowlisted fallback tools.

- [ ] **Step 6: Make API extraction report the actual tool and hide internal metadata**

Update `get_mcp_results()` to prefer `_harness.executed_tool_name` for `tool_name`, while returning only `data` and `source` from content.

- [ ] **Step 7: Add and verify retry/fallback trace tests**

Assert `attempt=1` retry, `attempt=2` completion, and one fallback event containing requested Tool, executed Tool, and error type without exception messages or raw arguments.

- [ ] **Step 8: Run focused regression tests**

```powershell
C:\Python\Python311\python.exe -m unittest tests.test_mcp_harness tests.test_mcp_result_utils tests.test_logging_utils -v
```

Expected: PASS; primary retry count and fallback execution order exactly match each test.

---

### Task 3: Real Pesticide Response Regression Fixture and Integration Test

**Files:**
- Create: `tests/fixtures/pesticide_usage_guide_list.json`
- Modify: `tests/test_mcp_harness.py`
- Modify: `tests/test_mcp_result_utils.py`

**Interfaces:**
- Consumes: `register_mcp_output_schemas()`, `process_mcp_tool_call()`, `get_mcp_results()`
- Produces: a network-independent regression test for the real `source`, `result_type`, `items`, and `item` shapes

- [ ] **Step 1: Add a sanitized real-response fixture**

The fixture must contain `status="success"`, `code="200"`, the real string `source`, and the observed pesticide candidate shape with `result_type`, `items`, and `item`. It must contain no token, personal information, or environment-specific identifier.

- [ ] **Step 2: Add a failing integration-flow test**

Register the pesticide Tool `outputSchema`, load the JSON fixture, execute `process_mcp_tool_call()`, wrap the validated result in a `ToolReturnPart`, and assert that `get_mcp_results()` returns the actual executed Tool name plus unchanged `data` and `source` without `_harness`.

- [ ] **Step 3: Run the integration test without network access**

```powershell
C:\Python\Python311\python.exe -m unittest tests.test_mcp_harness tests.test_mcp_result_utils -v
```

Expected: PASS and no call to the live MCP server.

---

### Task 4: Documentation Alignment

**Files:**
- Modify: `docs/superpowers/plans/2026-09-22-mcp-agent-priority-hardening.md`
- Modify: `docs/superpowers/plans/2026-09-23-mcp-agent-completion.md`

**Interfaces:**
- Consumes: verified implementation and test evidence from Tasks 1-3
- Produces: an accurate record of implemented behavior and the intentionally empty production fallback map

- [ ] **Step 1: Mark only executed and verified steps complete**

Update checkboxes only after their referenced commands pass. Record that fallback infrastructure exists but `FALLBACK_TOOL_MAP` intentionally remains empty because no compatible production pair has been approved.

- [ ] **Step 2: Record responsibility boundaries**

Document that the harness owns transient execution retry/fallback, PydanticAI propagates MCP errors, output validation errors are non-retryable, and the API exposes the actual executed Tool without internal metadata.

---

### Task 5: Final Verification and Cleanup

**Files:**
- Modify: `agent/app.py:92`
- Verify: all `agent/*.py`, `tests/*.py`, and plan documents

**Interfaces:**
- Consumes: all implementation tasks
- Produces: clean syntax, tests, whitespace, and a final manual trace

- [ ] **Step 1: Remove the trailing whitespace in `agent/app.py`**

Change `#서버 시작 전 툴 목록 가져오기 ` to `#서버 시작 전 툴 목록 가져오기`.

- [ ] **Step 2: Run syntax and full test verification**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Python\Python311\python.exe -m unittest discover -s tests -v
C:\Python\Python311\python.exe -c "import ast, pathlib; files=list(pathlib.Path('agent').glob('*.py'))+list(pathlib.Path('tests').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8')) for p in files]; print(f'ast_ok files={len(files)}')"
git diff --check
```

Expected: all tests PASS, syntax check prints `ast_ok`, and `git diff --check` produces no output.

- [ ] **Step 3: Run one normal multi-Tool manual request**

Use the existing list-then-detail pesticide request. Confirm one `execution_trace_id` links Tool start, validated result summary, completion, and final Agent decision logs, while the API returns unchanged `data` and `source`.

- [ ] **Step 4: Run fault-injection checks in a test environment**

Verify invalid output returns `FAILED` without retry, first-call transient failure retries once and succeeds, a 35-second Tool delay produces the configured timeout behavior, and the empty production fallback map does not invoke an unrelated Tool.

- [ ] **Step 5: Review the final diff before committing**

Confirm no `.env`, tokens, generated logs, caches, or unrelated user changes are staged. Commit each completed task separately with a focused message.
