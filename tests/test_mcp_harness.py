import asyncio
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from pydantic import ValidationError

sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent.mcp_harness import (
    # AGENT_USAGE_LIMITS,
    MCPServerConfig,
    MCPResultValidationError,
    discover_mcp_servers,
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
        for server_id in ("test", "agriculture", "law"):
            register_mcp_output_schemas(server_id, [])

    # Agent usage limits are intentionally disabled; the harness only owns MCP policies.
    # def test_agent_usage_limits_are_defined_in_harness(self):
    #     self.assertEqual(AGENT_USAGE_LIMITS.request_limit, 15)
    #     self.assertEqual(AGENT_USAGE_LIMITS.tool_calls_limit, 10)
    #     self.assertEqual(AGENT_USAGE_LIMITS.total_tokens_limit, 500_000)

    def test_server_config_uses_pydantic_validation(self):
        config = MCPServerConfig.model_validate(
            {
                "id": " agriculture ",
                "url": "https://agri.test/mcp",
                "prefix": " agriculture ",
            }
        )

        self.assertEqual(config.id, "agriculture")
        self.assertEqual(str(config.url), "https://agri.test/mcp")
        self.assertEqual(config.prefix, "agriculture")
        with self.assertRaises(ValidationError):
            MCPServerConfig.model_validate(
                {
                    "id": "agriculture",
                    "url": "ftp://agri.test/mcp",
                    "prefix": "agriculture",
                    "unexpected": True,
                }
            )

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
                [
                    MCPServerConfig(
                        id="one",
                        url="https://one.test/mcp",
                        prefix="one",
                    )
                ],
            )

    def test_rejects_tool_result_missing_schema_required_field(self):
        register_mcp_output_schemas(
            "test",
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "test",
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
            "test",
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "test",
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
            "test",
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )

        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "test",
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
            "test",
            [SimpleNamespace(name="sample", outputSchema=TOOL_OUTPUT_SCHEMA)]
        )
        expected = {
            "status": "success",
            "code": "200",
            "source": "농촌진흥청",
            "data": {"result_type": "candidates", "items": []},
        }

        result = validate_mcp_result("test", "sample", expected)

        self.assertIs(result, expected)

    def test_without_output_schema_skips_data_and_source_structure_validation(self):
        register_mcp_output_schemas("test", [])

        result = validate_mcp_result(
            "test",
            "sample",
            {
                "status": "success",
                "code": "200",
                "source": [],
            },
        )

        self.assertEqual(result["source"], [])

    def test_isolates_same_tool_name_schemas_by_server(self):
        agriculture_schema = {
            "type": "object",
            "required": ["data"],
            "properties": {
                "data": {
                    "type": "object",
                    "required": ["crops"],
                    "properties": {"crops": {"type": "array"}},
                }
            },
        }
        law_schema = {
            "type": "object",
            "required": ["data"],
            "properties": {
                "data": {
                    "type": "object",
                    "required": ["statutes"],
                    "properties": {"statutes": {"type": "array"}},
                }
            },
        }
        register_mcp_output_schemas(
            "agriculture",
            [SimpleNamespace(name="search", outputSchema=agriculture_schema)],
        )
        register_mcp_output_schemas(
            "law",
            [SimpleNamespace(name="search", outputSchema=law_schema)],
        )
        agriculture_result = {
            "status": "success",
            "code": "200",
            "data": {"crops": []},
        }
        law_result = {
            "status": "success",
            "code": "200",
            "data": {"statutes": []},
        }

        self.assertIs(
            validate_mcp_result("agriculture", "search", agriculture_result),
            agriculture_result,
        )
        self.assertIs(validate_mcp_result("law", "search", law_result), law_result)
        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result("agriculture", "search", law_result)
        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result("law", "search", agriculture_result)

    def test_reregistering_one_server_preserves_other_server_schemas(self):
        register_mcp_output_schemas(
            "agriculture",
            [SimpleNamespace(name="search", outputSchema={"required": ["agri"]})],
        )
        register_mcp_output_schemas(
            "law",
            [SimpleNamespace(name="search", outputSchema={"required": ["law"]})],
        )
        register_mcp_output_schemas("agriculture", [])

        law_result = {"status": "success", "code": "200", "law": True}
        self.assertIs(validate_mcp_result("law", "search", law_result), law_result)
        with self.assertRaises(MCPResultValidationError):
            validate_mcp_result(
                "law",
                "search",
                {"status": "success", "code": "200", "agri": True},
            )


def make_server(
    server_id: str,
    url: str,
    tools_or_error,
) -> tuple[MCPServerConfig, Mock]:
    toolset = Mock()
    if isinstance(tools_or_error, BaseException):
        toolset.list_tools = AsyncMock(side_effect=tools_or_error)
    else:
        toolset.list_tools = AsyncMock(return_value=tools_or_error)
    return MCPServerConfig(id=server_id, url=url, prefix=server_id), toolset


class MCPServerDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_returns_all_successful_servers_in_configuration_order(self):
        agriculture_tools = [SimpleNamespace(name="crop", outputSchema=None)]
        law_tools = [SimpleNamespace(name="statute", outputSchema=None)]
        agriculture, agriculture_toolset = make_server(
            "agriculture", "https://agri.test/mcp", agriculture_tools
        )
        law, law_toolset = make_server("law", "https://law.test/mcp", law_tools)

        with (
            patch(
                "agent.mcp_harness.MCPToolset",
                side_effect=[agriculture_toolset, law_toolset],
            ),
            patch("agent.mcp_harness.register_mcp_output_schemas") as register,
            patch("agent.mcp_harness.log_mcp_server_connected") as log_connected,
        ):
            result = await discover_mcp_servers(
                [agriculture, law], "Bearer shared-key"
            )

        self.assertEqual(
            [runtime.prefixed_toolset for runtime in result],
            [
                agriculture_toolset.prefixed.return_value,
                law_toolset.prefixed.return_value,
            ],
        )
        register.assert_has_calls(
            [call("agriculture", agriculture_tools), call("law", law_tools)]
        )
        log_connected.assert_has_calls(
            [
                call("agriculture", "https://agri.test/mcp", 1),
                call("law", "https://law.test/mcp", 1),
            ]
        )

    async def test_discovery_logs_and_excludes_failed_servers(self):
        failed, failed_toolset = make_server(
            "agriculture",
            "https://agri.test/mcp?secret=value",
            RuntimeError("private"),
        )
        healthy, healthy_toolset = make_server(
            "law",
            "https://law.test/mcp",
            [SimpleNamespace(name="statute", outputSchema=None)],
        )

        with (
            patch(
                "agent.mcp_harness.MCPToolset",
                side_effect=[failed_toolset, healthy_toolset],
            ),
            patch("agent.mcp_harness.log_mcp_server_connection_failed") as log_failed,
        ):
            result = await discover_mcp_servers(
                [failed, healthy], "Bearer shared-key"
            )

        self.assertEqual([runtime.config for runtime in result], [healthy])
        log_failed.assert_called_once()
        self.assertEqual(
            log_failed.call_args.args[:2],
            ("agriculture", str(failed.url)),
        )
        self.assertIsInstance(log_failed.call_args.args[2], RuntimeError)

    async def test_discovery_raises_when_all_servers_fail(self):
        agriculture, agriculture_toolset = make_server(
            "agriculture", "https://agri.test/mcp", TimeoutError()
        )
        law, law_toolset = make_server(
            "law", "https://law.test/mcp", RuntimeError()
        )

        with (
            patch(
                "agent.mcp_harness.MCPToolset",
                side_effect=[agriculture_toolset, law_toolset],
            ),
            self.assertLogs("uvicorn.error", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "No MCP servers connected"),
        ):
            await discover_mcp_servers([agriculture, law], "Bearer shared-key")

    async def test_discovery_propagates_cancellation(self):
        config, toolset = make_server(
            "agriculture",
            "https://agri.test/mcp",
            asyncio.CancelledError(),
        )

        with (
            patch("agent.mcp_harness.MCPToolset", return_value=toolset),
            self.assertRaises(asyncio.CancelledError),
        ):
            await discover_mcp_servers([config], "Bearer shared-key")


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
            await process_mcp_tool_call("test", TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_accepts_string_source_from_mcp_server(self):
        result_with_string_source = {
            **SUCCESS_RESULT,
            "source": "농촌진흥청 농약안전정보시스템 농약등록정보 검색서비스 API",
        }
        call_tool = SequenceToolCall([result_with_string_source])

        result = await process_mcp_tool_call(
            "test",
            TEST_CONTEXT,
            call_tool,
            "sample",
            {},
        )

        self.assertEqual(result, result_with_string_source)

    async def test_failed_status_raises_output_validation_error_without_retry(self):
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

        with self.assertRaises(MCPResultValidationError):
            await process_mcp_tool_call("test", TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_does_not_retry_failed_output_status(self):
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

        with self.assertRaises(MCPResultValidationError):
            await process_mcp_tool_call("test", TEST_CONTEXT, call_tool, "sample", {})

        self.assertEqual(call_tool.call_count, 1)

    async def test_does_not_retry_output_validation_error(self):
        self.addCleanup(register_mcp_output_schemas, "test", [])
        register_mcp_output_schemas(
            "test",
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
            await process_mcp_tool_call("test", TEST_CONTEXT, call_tool, "sample", {})

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
            returned = await process_mcp_tool_call(
                "test", TEST_CONTEXT, call_tool, "sample", args
            )

        logs = "\n".join(captured.output)
        self.assertEqual(returned, SUCCESS_RESULT)
        self.assertIn("trace-123", logs)
        self.assertIn("call-456", logs)
        self.assertIn("tool=sample", logs)
        self.assertIn("MCP TOOL CALL START", logs)
        self.assertEqual(logs.count("MCP TOOL CALL COMPLETE"), 1)
        self.assertNotIn("MCP TOOL RESULT", logs)
        for removed_field in ("status=", "code=", "data_type=", "result_type=", "item_count="):
            self.assertNotIn(removed_field, logs)
        self.assertNotIn("secret-api-key", logs)
        self.assertIn('"region": "수원"', logs)
        self.assertIn('"api_key": "***"', logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("010-1234-5678", logs)
        self.assertNotIn("secret-password", logs)

    async def test_tool_failure_attaches_context_without_duplicate_error_log(self):
        call_tool = SequenceToolCall([TimeoutError("private detail")])

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            with self.assertRaises(TimeoutError) as caught:
                await process_mcp_tool_call("test", TEST_CONTEXT, call_tool, "sample", {})

        logs = "\n".join(captured.output)
        self.assertNotIn("MCP TOOL CALL FAILED", logs)
        self.assertEqual(caught.exception.mcp_server_id, "test")
        self.assertEqual(caught.exception.mcp_tool_name, "sample")
        self.assertNotIn("private detail", logs)


if __name__ == "__main__":
    unittest.main()
