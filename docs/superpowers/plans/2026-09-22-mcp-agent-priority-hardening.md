# MCP Agent 우선순위 기능 강화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCP Agent에 Tool별 Output Validation, 안전한 Trace, 정책 기반 Fallback 실행을 추가한다.

**Architecture:** 현재 `MCPToolset → process_mcp_tool_call → AgentOutput` 흐름은 유지한다. 공통 검증은 `status="success"`, `code="200"`처럼 실행 성공 의미를 확인하고, MCP Server의 `outputSchema`는 `source`, `data` 및 Tool별 결과 구조와 타입을 검증한다. Trace에는 원문 데이터나 LLM 내부 추론 대신 관찰 가능한 실행 사실만 기록한다. Fallback은 재시도 가능한 장애가 모두 소진된 경우에만 명시적인 허용 목록 안에서 실행한다.

**Tech Stack:** Python 3.11+, FastAPI, PydanticAI, FastMCP, Pydantic, `jsonschema`, `unittest`

**Spec:** `C:\Users\user\Documents\카카오톡 받은 파일\AI_이삭이_MCP_Agent_Architecture_Specification.docx`

## Global Constraints

- 기존 네 상태 `SUCCESS`, `NEED_MORE_INFO`, `NOT_APPLICABLE`, `FAILED`를 변경하지 않는다.
- MCP Server가 반환한 `data`와 `source` 값은 정규화 과정에서 임의로 변환하지 않는다.
- `outputSchema`가 있는 Tool의 `source` 형식은 해당 Schema를 따르고, Schema가 없는 Tool의 최소 검증에서는 문자열과 객체를 모두 허용한다.
- Tool 결과 원문, 인증정보, 개인정보 및 LLM 내부 추론 과정은 로그에 기록하지 않는다.
- Output Validation 오류와 입력 오류에는 Retry/Fallback을 적용하지 않는다.
- Fallback은 동일 목적이며 입력 Schema가 호환되는 Tool을 명시적으로 등록한 경우에만 허용한다.
- 현재 제공된 23개 Tool에는 동일 목적의 확실한 대체 Tool이 없으므로 운영 Fallback 목록의 초기값은 비워둔다.
- 모든 변경은 실패 테스트를 먼저 추가하고 최소 구현 후 전체 회귀 테스트를 수행한다.

## Review Focus

- `outputSchema`가 있어도 `status="error"` 또는 `code!="200"`이면 공통 성공 검증에서 실패해야 한다.
- MCP Tool에 `outputSchema`가 없으면 공통 성공 검증과 최소 `data/source` 검증만 수행해야 한다.
- Tool별 필수 결과 필드가 누락되면 비재시도 검증 오류가 되어야 한다.
- 로그에는 Tool 결과 원문, API 키, 전화번호 및 예외 메시지가 노출되지 않아야 한다.
- 비재시도 오류와 검증 오류는 Fallback으로 전달되지 않아야 한다.
- Fallback 성공 시 API의 `tool_name`은 요청 Tool이 아니라 실제 실행 Tool을 나타내야 한다.

---

### Task 1: MCP outputSchema 등록 및 Tool별 결과 검증

**Files:**
- Modify: `pyproject.toml`
- Modify: `agent/mcp_harness.py:17-139`
- Modify: `agent/app.py:88-105`
- Test: `tests/test_mcp_harness.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces: `register_mcp_output_schemas(tools: Iterable[Any]) -> None`
- Produces: `validate_mcp_result(tool_name: str, result: Any) -> dict[str, Any]`
- Consumes: MCP Tool 정의의 `name`과 `outputSchema`

- [ ] **Step 1: `jsonschema` 의존성과 Schema 등록 실패 테스트 작성**

  `pyproject.toml`에 직접 의존성 `jsonschema`를 추가하고 잠금 파일을 갱신한다.

  ```powershell
  uv lock
  ```

  `tests/test_mcp_harness.py`에는 가짜 Tool 정의를 사용해 등록된 Schema가 Tool 이름별로 분리되는 테스트를 추가한다.

  ```python
  def test_registers_output_schema_by_tool_name(self):
      tools = [
          SimpleNamespace(
              name="sample",
              outputSchema={
                  "type": "object",
                  "required": ["status", "code", "source", "data"],
                  "properties": {
                      "status": {"type": "string"},
                      "code": {"type": "string"},
                      "source": {"type": "string"},
                      "data": {
                          "type": "object",
                          "required": ["result_type", "items"],
                          "properties": {
                              "result_type": {"const": "candidates"},
                              "items": {"type": "array"},
                          },
                      },
                  },
              },
          )
      ]
      register_mcp_output_schemas(tools)

      with self.assertRaises(MCPResultValidationError):
          validate_mcp_result(
              "sample",
              {
                  "status": "success",
                  "code": "200",
                  "source": "농촌진흥청",
                  "data": {"items": []},
              },
          )
  ```

  별도 테스트에서는 Schema상 문자열인 `code="400"`이 구조 검증을 통과하더라도 공통 성공 검증에서 실패하는지 확인한다. 이를 통해 공통 검증과 Schema 검증의 책임이 겹치지 않도록 고정한다.

- [ ] **Step 2: 실패 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest tests.test_mcp_harness -v
  ```

  Expected: `register_mcp_output_schemas`가 없어 ImportError 또는 검증 누락으로 FAIL.

- [ ] **Step 3: Schema 등록과 검증 최소 구현**

  `agent/mcp_harness.py`에 시작 시 한 번 갱신되는 Tool 이름별 Validator 저장소를 추가한다.

  ```python
  from collections.abc import Iterable
  from jsonschema import Draft202012Validator
  from jsonschema.exceptions import ValidationError

  _OUTPUT_VALIDATORS: dict[str, Draft202012Validator] = {}


  def register_mcp_output_schemas(tools: Iterable[Any]) -> None:
      _OUTPUT_VALIDATORS.clear()
      for tool in tools:
          schema = getattr(tool, "outputSchema", None)
          if schema:
              Draft202012Validator.check_schema(schema)
              _OUTPUT_VALIDATORS[tool.name] = Draft202012Validator(schema)
  ```

  `validate_mcp_result()`의 검증 순서를 다음과 같이 구성한다.

  1. 응답이 객체인지 확인한다.
  2. 공통 성공 의미인 `status="success"`, `code="200"`을 확인한다.
  3. 등록된 `outputSchema`가 있으면 응답 전체의 `source`, `data` 및 Tool별 구조를 검증한다.
  4. Schema가 없으면 기존 최소 조건인 `data` 존재와 `source` 문자열/객체 형식만 검증한다.

  공통 검증은 필드 타입을 반복 검사하지 않고 성공 여부만 판단한다. Schema 오류 메시지에는 실제 값 대신 Tool 이름과 실패한 필드 경로만 포함한다.

  ```python
  validator = _OUTPUT_VALIDATORS.get(tool_name)
  if validator is not None:
      try:
          validator.validate(result)
      except ValidationError as exc:
          field = ".".join(str(item) for item in exc.absolute_path) or "root"
          raise MCPResultValidationError(
              f"MCP tool {tool_name!r} returned an invalid field: {field}."
          ) from exc
  else:
      if "data" not in result:
          raise MCPResultValidationError(
              f"MCP tool {tool_name!r} returned no data field."
          )
      source = result.get("source")
      if source is not None and not isinstance(source, (str, Mapping)):
          raise MCPResultValidationError(
              f"MCP tool {tool_name!r} returned an invalid source field."
          )
  ```

- [ ] **Step 4: 서버 시작 시 Tool Schema 등록 연결**

  `agent/app.py`의 `lifespan()`에서 기존 `tools = await mcp_toolset.list_tools()` 직후 다음을 호출한다.

  ```python
  register_mcp_output_schemas(tools)
  ```

  Tool 목록 조회 실패는 기존처럼 서버 시작 실패로 유지한다. Tool에 `outputSchema`가 없으면 공통 성공 검증과 최소 `data/source` 검증이 적용되는 테스트도 추가한다.

- [ ] **Step 5: Task 1 테스트 및 전체 회귀 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest discover -s tests -v
  ```

  Expected: 공통 성공 검증, Schema 기반 `source/data` 구조 검증, Schema가 없는 Tool의 문자열·객체 `source`, Retry, Chat 처리 테스트를 포함해 모두 PASS.

- [ ] **Step 6: Task 1 커밋**

  ```powershell
  git add pyproject.toml uv.lock agent/mcp_harness.py agent/app.py tests/test_mcp_harness.py tests/test_chat.py
  git commit -m "feat: validate MCP results against tool output schemas"
  ```

---

### Task 2: Agent 판단 및 Tool 결과 Trace 보완

**Files:**
- Modify: `agent/logging_utils.py:31-169`
- Modify: `agent/mcp_harness.py:87-128`
- Modify: `agent/app.py:115-150`
- Test: `tests/test_logging_utils.py`
- Test: `tests/test_mcp_harness.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces: `log_mcp_tool_result(execution_trace_id: str, tool_name: str, tool_call_id: str, result: Mapping[str, Any]) -> None`
- Produces: `log_agent_decision(execution_trace_id: str, status: str, tool_count: int, missing_parameter_count: int) -> None`
- Consumes: Task 1에서 검증을 통과한 MCP 결과

- [ ] **Step 1: 민감정보 없는 Trace 실패 테스트 작성**

  `tests/test_logging_utils.py`에 다음 두 동작을 검증한다.

  ```python
  def test_tool_result_log_contains_summary_without_raw_data(self):
      result = {
          "status": "success",
          "code": "200",
          "source": "농촌진흥청",
          "data": {
              "result_type": "candidates",
              "items": [{"phone": "010-1234-5678", "name": "private"}],
          },
      }
      with self.assertLogs("uvicorn.error", level="INFO") as captured:
          log_mcp_tool_result("trace", "sample", "call", result)

      logs = "\n".join(captured.output)
      self.assertIn("result_type=candidates", logs)
      self.assertIn("item_count=1", logs)
      self.assertNotIn("010-1234-5678", logs)
      self.assertNotIn("private", logs)
  ```

  `log_agent_decision()`은 상태, 실행 Tool 수, 누락 Parameter 개수만 기록하며 질문·답변 원문이나 LLM 추론을 기록하지 않는 테스트를 추가한다.

- [ ] **Step 2: 실패 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest tests.test_logging_utils -v
  ```

  Expected: 새 로그 함수가 없어 FAIL.

- [ ] **Step 3: 안전한 결과 요약 로그 구현**

  `log_mcp_tool_result()`는 다음 필드만 계산해 기록한다.

  - `status`, `code`
  - `data`의 타입
  - `result_type` 문자열
  - `items`가 리스트일 경우 개수

  `data`, `source`, 사용자 질문 및 답변 원문은 기록하지 않는다. `process_mcp_tool_call()`에서 `validate_mcp_result()` 직후 호출한다.

- [ ] **Step 4: 최종 Agent 판단 로그 구현**

  `log_agent_decision()`을 추가하고 `AgentOutput` 생성 후 호출한다.

  ```python
  log_agent_decision(
      execution_trace_id,
      response.status,
      len(response.tool_results),
      len(response.missing_parameter),
  )
  ```

  이 로그는 문서의 “MCP Agent 판단”을 외부에서 확인 가능한 최종 상태로 기록한다. Tool 선택 이유나 내부 chain-of-thought는 기록하지 않는다.

- [ ] **Step 5: Trace 연계 및 회귀 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest discover -s tests -v
  ```

  Expected: Tool 시작 → 결과 검증 요약 → 완료 또는 오류 → Agent 상태가 동일 `execution_trace_id`로 추적되고 모든 테스트 PASS.

- [ ] **Step 6: Task 2 커밋**

  ```powershell
  git add agent/logging_utils.py agent/mcp_harness.py agent/app.py tests/test_logging_utils.py tests/test_mcp_harness.py tests/test_chat.py
  git commit -m "feat: add safe MCP decision and result tracing"
  ```

---

### Task 3: 명시적 허용 목록 기반 Fallback 실행

**Files:**
- Modify: `agent/mcp_harness.py:17-139`
- Modify: `agent/logging_utils.py:49-110`
- Modify: `agent/logging_utils.py:188-211`
- Test: `tests/test_mcp_harness.py`
- Test: `tests/test_logging_utils.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces: `FALLBACK_TOOL_MAP: dict[str, tuple[str, ...]]`
- Produces: `get_fallback_tools(tool_name: str) -> tuple[str, ...]`
- Produces: 내부 결과 Metadata `_harness.requested_tool_name`, `_harness.executed_tool_name`, `_harness.fallback_used`
- Consumes: 기존 `is_retryable_error(exc: Exception) -> bool`

- [ ] **Step 1: Fallback 정책 실패 테스트 작성**

  `tests/test_mcp_harness.py`에 다음 사례를 각각 추가한다.

  1. Primary Tool이 두 번 Timeout이고 등록된 Fallback이 성공하면 실제 결과를 반환한다.
  2. `400` Tool 오류에는 Fallback을 실행하지 않는다.
  3. Output Validation 오류에는 Fallback을 실행하지 않는다.
  4. 허용 목록이 비어 있으면 기존처럼 실패한다.
  5. Fallback도 실패하면 마지막 오류를 전파한다.

  ```python
  async def test_runs_approved_fallback_after_retryable_failure(self):
      calls = []

      async def call_tool(tool_name, args):
          calls.append(tool_name)
          if tool_name == "primary":
              raise TimeoutError("temporary")
          return SUCCESS_RESULT

      with patch.dict(FALLBACK_TOOL_MAP, {"primary": ("backup",)}, clear=True):
          result = await process_mcp_tool_call(
              TEST_CONTEXT,
              call_tool,
              "primary",
              {"region": "수원"},
          )

      self.assertEqual(calls, ["primary", "primary", "backup"])
      self.assertEqual(result["_harness"]["executed_tool_name"], "backup")
  ```

- [ ] **Step 2: 실패 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest tests.test_mcp_harness -v
  ```

  Expected: Fallback 정책과 실행 로직이 없어 FAIL.

- [ ] **Step 3: Retry와 Fallback 책임 분리**

  현재 반복문을 `_call_tool_with_retry()`로 추출한다. 이 함수는 하나의 Tool만 호출하며 성공 결과 또는 최종 예외를 반환한다. `process_mcp_tool_call()`은 Primary 실패 후 다음 조건을 모두 만족할 때만 대체 Tool을 순서대로 실행한다.

  ```text
  최종 오류가 retryable
  AND FALLBACK_TOOL_MAP에 Primary가 존재
  AND 대체 Tool 이름이 Primary와 다름
  ```

  Fallback에는 Primary와 같은 인자를 전달한다. 따라서 입력 Schema가 동일한 Tool만 허용 목록에 등록한다.

- [ ] **Step 4: 실제 실행 Tool 식별 Metadata 추가**

  검증 완료 후 결과를 복사하고 다음 내부 Metadata를 추가한다.

  ```python
  result["_harness"] = {
      "requested_tool_name": requested_tool_name,
      "executed_tool_name": executed_tool_name,
      "fallback_used": requested_tool_name != executed_tool_name,
  }
  ```

  `get_mcp_results()`는 `_harness.executed_tool_name`이 있으면 최종 `ToolResult.tool_name`으로 사용한다. `_harness` 자체는 API의 `data` 또는 `source`에 포함하지 않는다. `tool_call_id`는 원래 Agent 호출 ID를 유지한다.

- [ ] **Step 5: Fallback Trace 추가**

  `logging_utils.py`에 Primary, 대체 Tool, 오류 타입을 기록하는 함수를 추가한다.

  ```python
  log_mcp_tool_fallback(
      execution_trace_id,
      requested_tool_name,
      fallback_tool_name,
      type(exc).__name__,
  )
  ```

  예외 메시지, 인자 원문 및 결과 원문은 기록하지 않는다. 기존 `FAILED` 로그는 사용 가능한 Fallback까지 모두 실패한 시점에만 남긴다.

- [ ] **Step 6: 운영 Fallback 목록을 빈 상태로 명시**

  현재 23개 Tool은 이름이 유사해도 데이터와 목적이 다르므로 다음처럼 시작한다.

  ```python
  FALLBACK_TOOL_MAP: dict[str, tuple[str, ...]] = {}
  ```

  향후 Tool 소유자가 동일 목적, 동일 입력 Schema, 허용된 데이터 출처를 확인한 조합만 등록한다. 날씨/조기경보, 토양 물리/화학/단면 Tool처럼 목적이 다른 Tool은 대체 관계로 등록하지 않는다.

- [ ] **Step 7: Fallback 및 전체 회귀 테스트 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest discover -s tests -v
  C:\Python\Python311\python.exe -c "import ast, pathlib; files=list(pathlib.Path('agent').glob('*.py'))+list(pathlib.Path('tests').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8')) for p in files]; print(f'ast_ok files={len(files)}')"
  git diff --check
  ```

  Expected: 모든 테스트 PASS, 문법 검사 PASS, whitespace 오류 없음.

- [ ] **Step 8: Task 3 커밋**

  ```powershell
  git add agent/mcp_harness.py agent/logging_utils.py tests/test_mcp_harness.py tests/test_logging_utils.py tests/test_chat.py
  git commit -m "feat: add policy-controlled MCP tool fallback"
  ```

---

### Task 4: 실제 MCP Server 통합 검증 및 문서 정합성 확인

**Files:**
- Modify: `tests/test_mcp_harness.py`
- Reference: `C:\Users\user\Documents\카카오톡 받은 파일\AI_이삭이_MCP_Agent_Architecture_Specification.docx`

**Interfaces:**
- Consumes: Task 1의 Schema Validator, Task 2의 Trace, Task 3의 Fallback 정책
- Produces: 운영 적용 전 검증 결과와 승인된 Fallback 목록 관리 기준

- [ ] **Step 1: 실제 농약 Tool 응답 회귀 Fixture 작성**

  실제 응답과 동일한 문자열 `source`, `data.result_type`, `data.items`, `data.item` 구조를 Fixture로 고정한다. 네트워크 테스트가 아니라 단위 테스트로 유지하여 MCP Server 상태와 무관하게 재현 가능해야 한다.

- [ ] **Step 2: 통합 시나리오 테스트 작성**

  다음 시나리오를 하나의 흐름으로 검증한다.

  ```text
  Tool Schema 등록
  → Tool 호출
  → 공통 및 Tool별 Output Validation
  → 안전한 결과 Trace
  → tool_results에 실제 Tool 이름·data·source 반환
  ```

- [ ] **Step 3: 전체 검증 실행**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'
  C:\Python\Python311\python.exe -m unittest discover -s tests -v
  git diff --check
  ```

  Expected: 전체 PASS. 로그 캡처에서 원본 농약 데이터, 인증값, 개인정보가 노출되지 않음.

- [ ] **Step 4: 운영 전 수동 확인**

  MCP Server가 실행 중인 환경에서 기존 `/chat` 요청을 한 번 호출한다. 응답의 `status`, `tool_results[].tool_name`, `data`, `source`, `execution_trace_id`를 확인하고 동일 Trace ID로 Tool 선택·검증·완료 로그가 연결되는지 확인한다.

- [ ] **Step 5: 문서 정합성 기록**

  구현 후 아키텍처 문서의 다음 항목에 실제 상태를 반영한다.

  - Output Validation: 공통 성공 의미(`status/code`) + MCP `outputSchema` 구조 검증(`source/data` 및 Tool별 결과)
  - Trace: Agent 최종 상태 + Tool 선택/입력/검증 요약/오류/재시도/Fallback
  - Fallback: 명시적 허용 목록 방식이며 현재 운영 목록은 비어 있음

- [ ] **Step 6: 최종 커밋**

  ```powershell
  git add tests/test_mcp_harness.py
  git commit -m "test: verify MCP harness integration flow"
  ```
