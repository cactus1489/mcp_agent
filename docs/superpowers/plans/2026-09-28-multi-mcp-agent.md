# Multi-MCP Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the PydanticAI agent to every healthy server declared in `MCP_SERVERS`, apply one global `MCP_API_KEY` to all servers, prefix their tools, and tolerate individual startup connection failures with sanitized logs.

**Architecture:** Parse the JSON configuration into immutable server records, construct one raw and one prefixed toolset per server, then probe raw toolsets concurrently during FastAPI lifespan startup. Bind result validation to `(server_id, original_tool_name)` and construct the Agent only from healthy prefixed toolsets, storing it in FastAPI application state.

**Tech Stack:** Python 3.11+, FastAPI lifespan/dependencies, PydanticAI `MCPToolset`, `asyncio`, `jsonschema`, `unittest`, `unittest.mock`

**Spec:** `docs/superpowers/specs/2026-09-28-multi-mcp-design.md`

## Global Constraints

- Read server definitions exclusively from the `MCP_SERVERS` JSON array.
- Apply the exact value of global `MCP_API_KEY` as the `Authorization` header for every server.
- Require unique, non-empty `id` and `prefix` values and absolute `http` or `https` URLs.
- Prefix every model-visible tool name with its configured server prefix.
- One healthy MCP server is sufficient for startup; zero healthy servers fails startup.
- Never disable TLS verification.
- Never log API keys, authorization headers, URL user information, query strings, fragments, or exception messages.
- Do not make live network calls in unit tests.
- Preserve the user's existing uncommitted comment and formatting changes in `agent/app.py`, `agent/logging_utils.py`, `agent/mcp_harness.py`, and `agent/models.py` unless a touched line must change for this feature.
- Do not add runtime reconnection, periodic health checks, or per-server credentials.

## Review Focus

- Empty arrays, non-array JSON, whitespace-only IDs/prefixes, and duplicate IDs/prefixes must fail configuration parsing; Task 1 pins these cases.
- URLs containing user information, query parameters, fragments, IPv6 hosts, or explicit ports must never leak sensitive components in logs; Task 2 pins sanitization.
- Identically named tools with different schemas on different servers must validate against their own schema; Task 2 pins isolation.
- Mixed TLS/authentication/timeout failures must not exclude healthy servers, while all-failed discovery must fail startup; Task 3 pins both outcomes.
- Task cancellation during startup must propagate instead of being converted into an ordinary unavailable-server result; Task 3 pins cancellation behavior.

---

## File Structure

- `agent/mcp_harness.py`: configuration parsing, server runtime construction, server-bound tool processing, schema isolation, and concurrent startup discovery.
- `agent/logging_utils.py`: server-aware tool logs and sanitized MCP connection logs.
- `agent/app.py`: Agent factory, FastAPI application-state dependency, and lifespan orchestration.
- `tests/test_mcp_harness.py`: configuration, factory, schema isolation, callback, and discovery tests with mocked MCP clients.
- `tests/test_logging_utils.py`: server identity and URL/error sanitization tests.
- `tests/test_chat.py`: application-state Agent construction and endpoint integration tests.
- `.env`: local `MCP_SERVERS` value; retain the existing secret `MCP_API_KEY` unchanged.

### Task 1: Multi-server configuration and runtime factory

**Files:**
- Modify: `agent/mcp_harness.py`
- Modify: `tests/test_mcp_harness.py`

**Interfaces:**
- Produces: `MCPServerConfig(id: str, url: str, prefix: str)` immutable dataclass.
- Produces: `MCPServerRuntime(config: MCPServerConfig, toolset: MCPToolset, prefixed_toolset: AbstractToolset[Any])` immutable dataclass.
- Produces: `parse_mcp_server_configs(raw: str) -> list[MCPServerConfig]`.
- Produces: `load_mcp_server_configs() -> list[MCPServerConfig]`, reading `os.environ["MCP_SERVERS"]`.
- Produces: `create_mcp_server_runtimes(configs: Iterable[MCPServerConfig], api_key: str) -> list[MCPServerRuntime]`.
- Consumes: the existing MCP retry, error, initialization-timeout, read-timeout, and tool-processing policies.

- [ ] **Step 1: Write failing parser tests**

Add tests named `test_parses_multi_server_json`, `test_rejects_invalid_top_level_or_empty_server_list`, `test_rejects_blank_or_duplicate_id_and_prefix`, and `test_rejects_non_http_absolute_urls`. Assert the valid example returns exactly the `agriculture` and `law` records from the spec, while malformed JSON, `{}`, `[]`, whitespace-only fields, duplicate values, relative URLs, and `ftp` URLs raise `ValueError` without echoing the raw JSON.

- [ ] **Step 2: Run the parser tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: FAIL because `MCPServerConfig` and `parse_mcp_server_configs` do not exist.

- [ ] **Step 3: Implement configuration parsing**

In `agent/mcp_harness.py`, add the frozen `MCPServerConfig` dataclass and implement `parse_mcp_server_configs(raw: str) -> list[MCPServerConfig]` plus `load_mcp_server_configs() -> list[MCPServerConfig]`. Use `json.loads`, require a non-empty list of objects with exactly `id`, `url`, and `prefix`, trim and reject blank identifiers, validate URLs with `urllib.parse.urlsplit`, and reject duplicate IDs or prefixes.

- [ ] **Step 4: Run the parser tests and verify they pass**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: parser tests PASS; the old single-server factory test may still fail until the next steps replace it.

- [ ] **Step 5: Replace the single-server factory test with shared-key runtime tests**

Add `test_creates_prefixed_runtime_for_each_server_with_shared_authorization` and `test_loads_configs_from_mcp_servers_environment`. Patch `MCPToolset`, assert it is called once per URL with `id=config.id`, the same `headers={"Authorization": shared_key}`, and the existing policy values; assert `.prefixed(config.prefix)` supplies each runtime's `prefixed_toolset`.

- [ ] **Step 6: Run the runtime tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: FAIL because `MCPServerRuntime` and `create_mcp_server_runtimes` do not exist.

- [ ] **Step 7: Implement runtime construction**

Add frozen `MCPServerRuntime` and implement `create_mcp_server_runtimes(configs: Iterable[MCPServerConfig], api_key: str) -> list[MCPServerRuntime]`. Instantiate one raw `MCPToolset` per config, set `id`, pass the global authorization value and existing policy, then store both the raw toolset and `toolset.prefixed(config.prefix)`.

- [ ] **Step 8: Run Task 1 tests**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: all configuration and factory tests PASS; unrelated callback tests remain compatible or are updated in Task 2.

- [ ] **Step 9: Commit Task 1**

```bash
git add agent/mcp_harness.py tests/test_mcp_harness.py
git commit -m "feat: configure multiple MCP servers"
```

### Task 2: Server-scoped schemas and sanitized logging

**Files:**
- Modify: `agent/mcp_harness.py`
- Modify: `agent/logging_utils.py`
- Modify: `tests/test_mcp_harness.py`
- Modify: `tests/test_logging_utils.py`

**Interfaces:**
- Consumes: `MCPServerConfig` and `create_mcp_server_runtimes` from Task 1.
- Produces: `register_mcp_output_schemas(server_id: str, tools: Iterable[Any]) -> None`.
- Produces: `validate_mcp_result(server_id: str, tool_name: str, result: Any) -> dict[str, Any]`.
- Produces: `process_mcp_tool_call(server_id: str, ctx: Any, call_tool: Callable, tool_name: str, args: dict[str, Any]) -> Any`.
- Produces: `log_mcp_server_connected(server_id: str, url: str, tool_count: int) -> None`.
- Produces: `log_mcp_server_connection_failed(server_id: str, url: str, exc: BaseException) -> None`.
- Changes: all `log_mcp_tool_*` functions receive `server_id` immediately after `execution_trace_id`.

- [ ] **Step 1: Write failing schema-isolation tests**

Update existing validator tests to pass a server ID. Add `test_isolates_same_tool_name_schemas_by_server` that registers incompatible `search` schemas for `agriculture` and `law`, verifies each matching result passes, and verifies cross-domain results fail. Add `test_reregistering_one_server_preserves_other_server_schemas` to prove registration removes only the selected server's old keys.

- [ ] **Step 2: Run schema tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: FAIL because validators are still keyed only by tool name and registration clears the full map.

- [ ] **Step 3: Implement compound-key validation**

Change `_OUTPUT_VALIDATORS` to `dict[tuple[str, str], Draft202012Validator]`. Update registration to remove only entries whose first tuple element equals `server_id`, then add `(server_id, tool.name)`. Update validation and `process_mcp_tool_call` signatures to carry `server_id`. In Task 1's runtime factory, bind `config.id` into the MCP callback with `functools.partial`.

- [ ] **Step 4: Run schema and callback tests**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: all schema and callback tests PASS with explicit server IDs.

- [ ] **Step 5: Write failing server-aware logging tests**

Update existing tool-log assertions to require `server=agriculture`. Add `test_connection_logs_sanitize_url_and_exception` using a URL with user information, IPv6 host, explicit port, query, and fragment plus an exception containing a secret. Assert logs retain only server ID, bracketed IPv6 host, port, tool count or exception type, and contain none of the user information, query, fragment, exception message, or API key.

- [ ] **Step 6: Run logging tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_logging_utils.py" -v`

Expected: FAIL because connection log functions and server-aware tool log signatures do not exist.

- [ ] **Step 7: Implement safe connection and server-aware tool logs**

In `agent/logging_utils.py`, add a private URL-to-host formatter using `urlsplit().hostname` and `.port`, formatting IPv6 hosts with brackets. Add the two connection log functions and update all tool log formats/signatures to include `server=%s`. Log only `type(exc).__name__` for connection failures.

- [ ] **Step 8: Run Task 2 tests**

Run: `python -m unittest discover -s tests -p "test_*logging*.py" -v`

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: both commands PASS.

- [ ] **Step 9: Commit Task 2**

```bash
git add agent/mcp_harness.py agent/logging_utils.py tests/test_mcp_harness.py tests/test_logging_utils.py
git commit -m "feat: isolate MCP schemas and logs by server"
```

### Task 3: Concurrent discovery with partial-failure tolerance

**Files:**
- Modify: `agent/mcp_harness.py`
- Modify: `tests/test_mcp_harness.py`

**Interfaces:**
- Consumes: `MCPServerRuntime`, scoped schema registration, and connection logs from Tasks 1-2.
- Produces: `discover_mcp_servers(runtimes: Sequence[MCPServerRuntime]) -> list[MCPServerRuntime]`.
- Behavior: probes every raw `runtime.toolset.list_tools()` concurrently, registers schemas for successful servers, logs every result, returns healthy runtimes in configuration order, raises `RuntimeError("No MCP servers connected.")` when none succeed, and propagates cancellation.

- [ ] **Step 1: Write failing discovery tests**

Add asynchronous tests `test_discovery_returns_all_successful_servers_in_configuration_order`, `test_discovery_logs_and_excludes_failed_servers`, `test_discovery_raises_when_all_servers_fail`, and `test_discovery_propagates_cancellation`. Use `AsyncMock` toolsets only; assert successful tools are registered under their server IDs, each failure log receives the matching config, and no network transport is constructed by the test.

- [ ] **Step 2: Run discovery tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: FAIL because `discover_mcp_servers` does not exist.

- [ ] **Step 3: Implement concurrent discovery**

Implement `discover_mcp_servers` with one probe coroutine per runtime and `asyncio.gather(..., return_exceptions=True)`. Pair results with runtimes in input order. Re-raise `asyncio.CancelledError`, log and exclude other failures, register output schemas and log tool counts for successes, and raise the exact all-failed `RuntimeError` after every failure has been logged.

- [ ] **Step 4: Run Task 3 tests**

Run: `python -m unittest discover -s tests -p "test_mcp_harness.py" -v`

Expected: all harness tests PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add agent/mcp_harness.py tests/test_mcp_harness.py
git commit -m "feat: tolerate partial MCP startup failures"
```

### Task 4: FastAPI lifespan integration and local configuration migration

**Files:**
- Modify: `agent/app.py`
- Modify: `tests/test_chat.py`
- Modify locally: `.env` (ignored secret-bearing file; do not commit)

**Interfaces:**
- Consumes: `load_mcp_server_configs`, `create_mcp_server_runtimes`, and `discover_mcp_servers` from Tasks 1 and 3.
- Produces: `create_agent(toolsets: Sequence[AbstractToolset[Any]]) -> Agent` using the existing model, output type, retries, model settings, and instructions unchanged.
- Produces: `get_agent(request: fastapi.Request) -> Agent`, returning `request.app.state.agent`.
- Changes: `chat(request: ChatRequest, runtime_agent: Agent = Depends(get_agent)) -> AgentOutput` uses the injected Agent.
- Behavior: lifespan builds runtimes from `MCP_SERVERS` plus global `MCP_API_KEY`, discovers healthy servers, stores the resulting Agent at `app.state.agent`, and removes it on shutdown.

- [ ] **Step 1: Write failing lifespan and endpoint tests**

Replace the single `mcp_toolset.list_tools()` lifespan test with `test_lifespan_builds_agent_from_only_healthy_prefixed_toolsets` and `test_lifespan_fails_when_discovery_has_no_healthy_server`. Update chat tests to inject an `AsyncMock` Agent directly, and add `test_get_agent_returns_lifespan_agent`. Assert the Agent factory receives only healthy prefixed toolsets and existing output/retry/instruction behavior is preserved.

- [ ] **Step 2: Run chat tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_chat.py" -v`

Expected: FAIL because Agent creation is still module-level and the endpoint has no application-state dependency.

- [ ] **Step 3: Refactor Agent construction and lifespan**

Move the current `Agent(...)` construction into `create_agent`. Remove module-level `mcp_toolset` and `agent`. Implement `get_agent`, update `/chat` to use its injected Agent, and update lifespan to load configs, create runtimes with `os.environ["MCP_API_KEY"]`, discover healthy servers, create the Agent from `runtime.prefixed_toolset`, assign `app.state.agent`, yield, and delete the state attribute in `finally`.

- [ ] **Step 4: Run chat tests and verify they pass**

Run: `python -m unittest discover -s tests -p "test_chat.py" -v`

Expected: all chat tests PASS.

- [ ] **Step 5: Migrate the local environment setting without exposing its secret**

In `.env`, replace only `MCP_SERVER_URL=...` with this single-line value and leave the existing `MCP_API_KEY` line unchanged:

```dotenv
MCP_SERVERS=[{"id":"agriculture","url":"https://aic.iteyes.io:8101/mcp","prefix":"agriculture"},{"id":"law","url":"https://mcp.gomdori.app/law?oc=honggildong","prefix":"law"}]
```

Do not print, stage, or commit `.env`.

- [ ] **Step 6: Run the complete offline unit suite**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS with no attempt to contact either MCP URL.

- [ ] **Step 7: Verify no legacy single-server dependency remains**

Run: `Get-ChildItem agent,tests -Recurse -File | Select-String -Pattern 'MCP_SERVER_URL|create_mcp_toolset|mcp_toolset'`

Expected: no legacy production reference; test names or explicit negative assertions are acceptable only when intentional.

- [ ] **Step 8: Review the final diff for secret and user-change safety**

Run: `git diff -- agent tests`

Expected: no API key values or authorization secrets appear, no TLS verification bypass exists, and the user's pre-existing comment-only changes remain intact.

- [ ] **Step 9: Commit Task 4 without `.env`**

```bash
git add agent/app.py tests/test_chat.py
git commit -m "feat: initialize agent from healthy MCP servers"
```

### Task 5: Final verification

**Files:**
- Verify only: `agent/`, `tests/`, `docs/superpowers/specs/2026-09-28-multi-mcp-design.md`

**Interfaces:**
- Consumes: all prior task deliverables.
- Produces: evidence that the complete implementation satisfies the approved spec without a live MCP connection.

- [ ] **Step 1: Run the full unit suite from a clean Python process**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS.

- [ ] **Step 2: Compile production modules**

Run: `python -m compileall -q agent`

Expected: exit code 0 with no syntax errors.

- [ ] **Step 3: Inspect repository status and committed scope**

Run: `git status --short`

Expected: only the user's pre-existing unrelated modifications and ignored `.env` remain; no secret-bearing file is staged or committed.

- [ ] **Step 4: Perform spec-to-implementation review**

Confirm configuration validation, shared authentication, prefixing, compound schema keys, partial startup, all-failed startup, sanitized logs, application-state Agent injection, and offline-only tests each have a passing test and matching implementation.
