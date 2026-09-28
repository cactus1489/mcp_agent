import json
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent.mcp_harness import (
    AGENT_USAGE_LIMITS,
    MCPServerConfig,
    MCPResultValidationError,
    MCPToolResultError,
    create_mcp_server_runtimes,
    load_mcp_server_configs,
    parse_mcp_server_configs,
    process_mcp_tool_call,
    register_mcp_output_schemas,
    validate_mcp_result,
)


SUCCESS_RESULT = {
    "status": "success",
    "code": "200",
    "msg": "",
    "data": {"value": 1},
}

TEST_CONTEXT = SimpleNamespace(
    run_id="trace-123",
    tool_call_id="call-456",
    run_step=1,
    retry=0,
    max_retries=1,
)

TOOL_OUTPUT_SCHEMA = {
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
}

class SequenceToolCall:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.call_count = 0

    async def __call__(self, tool_name, args):
        self.call_count += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MCPHarnessConfigurationTests(unittest.TestCase):
    def tearDown(self):
        register_mcp_output_schemas([])

    def test_agent_usage_limits_are_defined_in_harness(self):
        self.assertEqual(AGENT_USAGE_LIMITS.request_limit, 15)
        self.assertEqual(AGENT_USAGE_LIMITS.tool_calls_limit, 10)
        self.assertEqual(AGENT_USAGE_LIMITS.total_tokens_limit, 500_000)

    def test_parses_multi_server_json(self):
        raw = (
            '[{"id":"agriculture","url":"https://aic.iteyes.io:8101/mcp",'
            '"prefix":"agriculture"},{"id":"law",'
            '"url":"https://mcp.gomdori.app/law?oc=honggildong",'
            '"prefix":"law"}]'
        )

        self.assertEqual(
            parse_mcp_server_configs(raw),
            [
                MCPServerConfig(
                    id="agriculture",
                    url="https://aic.iteyes.io:8101/mcp",
                    prefix="agriculture",
                ),
                MCPServerConfig(
                    id="law",
                    url="https://mcp.gomdori.app/law?oc=honggildong",
                    prefix="law",
                ),
            ],
        )

    def test_rejects_invalid_top_level_or_empty_server_list(self):
        for raw in ("not-json", "{}", "[]"):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                parse_mcp_server_configs(raw)
            self.assertNotIn(raw, str(caught.exception))

    def test_rejects_blank_or_duplicate_id_and_prefix(self):
        invalid_values = (
            '[{"id":" ","url":"https://one.test/mcp","prefix":"one"}]',
            '[{"id":"one","url":"https://one.test/mcp","prefix":" "}]',
            '[{"id":"same","url":"https://one.test/mcp","prefix":"one"},'
            '{"id":"same","url":"https://two.test/mcp","prefix":"two"}]',
            '[{"id":"one","url":"https://one.test/mcp","prefix":"same"},'
            '{"id":"two","url":"https://two.test/mcp","prefix":"same"}]',
        )
        for raw in invalid_values:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_mcp_server_configs(raw)

    def test_rejects_non_http_absolute_urls(self):
        for url in ("/mcp", "ftp://example.test/mcp", "https:///mcp"):
            raw = json.dumps([{"id": "one", "url": url, "prefix": "one"}])
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_mcp_server_configs(raw)

    def test_loads_configs_from_mcp_servers_environment(self):
        raw = '[{"id":"one","url":"https://one.test/mcp","prefix":"one"}]'

        with patch.dict(os.environ, {"MCP_SERVERS": raw}, clear=False):
            self.assertEqual(
                load_mcp_server_configs(),
                [MCPServerConfig("one", "https://one.test/mcp", "one")],
            )

    def test_creates_prefixed_runtime_for_each_server_with_shared_authorization(self):
        configs = [
            MCPServerConfig("agriculture", "https://agri.test/mcp", "agriculture"),
            MCPServerConfig("law", "https://law.test/mcp", "law"),
        ]
        agriculture_toolset = Mock()
        law_toolset = Mock()

        with patch(
            "agent.mcp_harness.MCPToolset",
            side_effect=[agriculture_toolset, law_toolset],
        ) as toolset_class:
            runtimes = create_mcp_server_runtimes(configs, "Bearer shared-key")

        self.assertEqual([runtime.config for runtime in runtimes], configs)
        self.assertIs(runtimes[0].toolset, agriculture_toolset)
        self.assertIs(runtimes[0].prefixed_toolset, agriculture_toolset.prefixed.return_value)
        self.assertIs(runtimes[1].toolset, law_toolset)
        self.assertIs(runtimes[1].prefixed_toolset, law_toolset.prefixed.return_value)
        agriculture_toolset.prefixed.assert_called_once_with("agriculture")
        law_toolset.prefixed.assert_called_once_with("law")
        toolset_class.assert_has_calls(
            [
                call(
                    "https://agri.test/mcp",
                    id="agriculture",
                    headers={"Authorization": "Bearer shared-key"},
                    max_retries=1,
                    tool_error_behavior="retry",
                    init_timeout=10,
                    read_timeout=30,
                    process_tool_call=process_mcp_tool_call,
                ),
                call(
                    "https://law.test/mcp",
                    id="law",
                    headers={"Authorization": "Bearer shared-key"},
                    max_retries=1,
                    tool_error_behavior="retry",
                    init_timeout=10,
                    read_timeout=30,
                    process_tool_call=process_mcp_tool_call,
                ),
            ]
        )

    def test_rejects_tool_result_missing_schema_required_field(self):
        register_mcp_output_schemas(
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

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

    def test_rejects_source_type_that_conflicts_with_tool_schema(self):
        register_mcp_output_schemas(
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "sample",
                {
                    "status": "success",
                    "code": "200",
                    "source": {"provider": "농촌진흥청"},
                    "data": {"result_type": "candidates", "items": []},
                },
            )

    def test_rejects_non_success_code_even_when_schema_allows_string(self):
        register_mcp_output_schemas(
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "sample",
                {
                    "status": "success",
                    "code": "400",
                    "source": "농촌진흥청",
                    "data": {"result_type": "candidates", "items": []},
                },
            )

    def test_accepts_result_matching_tool_output_schema(self):
        register_mcp_output_schemas(
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )
        expected = {
            "status": "success",
            "code": "200",
            "source": "농촌진흥청",
            "data": {"result_type": "candidates", "items": []},
        }

        result = validate_mcp_result("sample", expected)

        self.assertIs(result, expected)

    def test_without_output_schema_skips_data_and_source_structure_validation(self):
        register_mcp_output_schemas([])

        result = validate_mcp_result(
            "sample",
            {
                "status": "success",
                "code": "200",
                "source": [],
            },
        )

        self.assertEqual(result["source"], [])


class ProcessMCPToolCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_logger = logging.getLogger("uvicorn.error")
        self.original_handlers = self.test_logger.handlers
        self.original_propagate = self.test_logger.propagate
        self.test_logger.handlers = [logging.NullHandler()]
        self.test_logger.propagate = False

    def tearDown(self):
        self.test_logger.handlers = self.original_handlers
        self.test_logger.propagate = self.original_propagate

    async def test_timeout_propagates_without_custom_retry(self):
        call_tool = SequenceToolCall([TimeoutError("temporary")])

        with self.assertRaises(TimeoutError):
            await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_accepts_string_source_from_mcp_server(self):
        result_with_string_source = {
            **SUCCESS_RESULT,
            "source": "농촌진흥청 농약안전정보시스템 농약등록정보 검색서비스 API",
        }
        call_tool = SequenceToolCall([result_with_string_source])

        result = await process_mcp_tool_call(
            TEST_CONTEXT,
            call_tool,
            "sample",
            {},
        )

        self.assertEqual(result, result_with_string_source)

    async def test_tool_result_error_propagates_without_custom_retry(self):
        call_tool = SequenceToolCall(
            [
                {
                    "status": "error",
                    "code": "503",
                    "msg": "temporarily unavailable",
                    "data": None,
                },
            ]
        )

        with self.assertRaises(MCPToolResultError):
            await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_does_not_retry_non_retryable_tool_error(self):
        call_tool = SequenceToolCall(
            [
                {
                    "status": "error",
                    "code": "400",
                    "msg": "invalid input",
                    "data": None,
                },
                SUCCESS_RESULT,
            ]
        )

        with self.assertRaises(MCPToolResultError):
            await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_does_not_retry_output_validation_error(self):
        self.addCleanup(register_mcp_output_schemas, [])
        register_mcp_output_schemas(
            [
                SimpleNamespace(
                    name="sample",
                    outputSchema={
                        "type": "object",
                        "required": ["data"],
                    },
                )
            ]
        )
        call_tool = SequenceToolCall(
            [
                {"status": "success", "code": "200"},
                SUCCESS_RESULT,
            ]
        )

        with self.assertRaises(MCPResultValidationError):
            await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_logs_tool_trace_and_masks_sensitive_arguments(self):
        call_tool = SequenceToolCall([SUCCESS_RESULT])
        args = {
            "region": "수원",
            "api_key": "secret-api-key",
            "nested": {
                "Authorization": "Bearer secret-token",
                "phone": "010-1234-5678",
            },
            "items": [{"password": "secret-password"}],
        }

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", args)

        logs = "\n".join(captured.output)
        self.assertIn("trace-123", logs)
        self.assertIn("call-456", logs)
        self.assertIn("tool=sample", logs)
        self.assertIn("MCP TOOL CALL START", logs)
        self.assertIn("MCP TOOL RESULT", logs)
        self.assertIn("MCP TOOL CALL COMPLETE", logs)
        self.assertIn("status=success", logs)
        self.assertIn("code=200", logs)
        self.assertIn("data_type=dict", logs)
        self.assertNotIn("secret-api-key", logs)
        self.assertIn('"region": "수원"', logs)
        self.assertIn('"api_key": "***"', logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("010-1234-5678", logs)
        self.assertNotIn("secret-password", logs)

    async def test_logs_tool_failure_without_exposing_exception_details(self):
        call_tool = SequenceToolCall([TimeoutError("private detail")])

        with self.assertLogs("uvicorn.error", level="ERROR") as captured:
            with self.assertRaises(TimeoutError):
                await process_mcp_tool_call(TEST_CONTEXT, call_tool, "sample", {})

        logs = "\n".join(captured.output)
        self.assertIn("MCP TOOL CALL FAILED", logs)
        self.assertIn("attempt=1", logs)
        self.assertIn("error_type=TimeoutError", logs)
        self.assertNotIn("private detail", logs)


if __name__ == "__main__":
    unittest.main()
