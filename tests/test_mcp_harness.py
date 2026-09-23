import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent.mcp_harness import (
    AGENT_USAGE_LIMITS,
    MCPResultValidationError,
    MCPToolResultError,
    create_mcp_toolset,
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

    def test_create_mcp_toolset_uses_harness_connection_policy(self):
        env = {
            "MCP_SERVER_URL": "https://mcp.example.test",
            "MCP_API_KEY": "test-key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("agent.mcp_harness.MCPToolset") as toolset_class,
        ):
            result = create_mcp_toolset()

        self.assertIs(result, toolset_class.return_value)
        toolset_class.assert_called_once_with(
            env["MCP_SERVER_URL"],
            headers={"Authorization": env["MCP_API_KEY"]},
            max_retries=1,
            tool_error_behavior="retry",
            init_timeout=10,
            read_timeout=30,
            process_tool_call=process_mcp_tool_call,
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
