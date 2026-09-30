import sys
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent import app as app_module
from agent.mcp_harness import MCPServerConfig
from agent.models import ChatRequest


class LifecycleAgent:
    def __init__(self):
        self.active = False

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *args):
        self.active = False


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_logger = logging.getLogger("uvicorn.error")
        self.original_handlers = self.test_logger.handlers
        self.original_propagate = self.test_logger.propagate
        self.test_logger.handlers = [logging.NullHandler()]
        self.test_logger.propagate = False

    def tearDown(self):
        self.test_logger.handlers = self.original_handlers
        self.test_logger.propagate = self.original_propagate

    async def test_result_processing_error_returns_failed_response(self):
        result = SimpleNamespace(
            output=SimpleNamespace(
                status="NOT_APPLICABLE",
                missing_parameter=[],
                answer="unused",
            ),
            all_messages=Mock(side_effect=RuntimeError("processing failed")),
        )

        runtime_agent = SimpleNamespace(run=AsyncMock(return_value=result))
        with patch.object(app_module, "write_qa_audit_log"):
            response = None
            caught = None
            try:
                response = await app_module.chat(
                    ChatRequest(request="test"),
                    runtime_agent=runtime_agent,
                )
            except Exception as exc:
                caught = exc

        self.assertIsNone(caught)
        self.assertEqual(response.status, "FAILED")

    async def test_lifespan_builds_agent_from_only_healthy_prefixed_toolsets(self):
        configs = [
            MCPServerConfig(
                id="agriculture",
                url="https://agri.test/mcp",
                prefix="agriculture",
            )
        ]
        healthy = [Mock()]
        created_agent = LifecycleAgent()

        with (
            patch.dict(os.environ, {"MCP_API_KEY": "Bearer shared"}, clear=False),
            patch.object(app_module, "load_mcp_server_configs", return_value=configs),
            patch.object(
                app_module,
                "discover_mcp_servers",
                AsyncMock(return_value=healthy),
            ) as discover,
            patch.object(app_module, "create_agent", return_value=created_agent) as create_agent,
        ):
            async with app_module.lifespan(app_module.app):
                self.assertIs(app_module.app.state.agent, created_agent)
                self.assertTrue(created_agent.active)

        discover.assert_awaited_once_with(configs, "Bearer shared")
        create_agent.assert_called_once_with([healthy[0].prefixed_toolset])
        self.assertFalse(created_agent.active)
        self.assertFalse(hasattr(app_module.app.state, "agent"))

    async def test_lifespan_fails_when_discovery_has_no_healthy_server(self):
        with (
            patch.dict(os.environ, {"MCP_API_KEY": "Bearer shared"}, clear=False),
            patch.object(app_module, "load_mcp_server_configs", return_value=[Mock()]),
            patch.object(
                app_module,
                "discover_mcp_servers",
                AsyncMock(side_effect=RuntimeError("No MCP servers connected.")),
            ),
            patch.object(app_module, "create_agent") as create_agent,
        ):
            with self.assertRaisesRegex(RuntimeError, "No MCP servers connected"):
                async with app_module.lifespan(app_module.app):
                    pass

        create_agent.assert_not_called()

    def test_get_agent_returns_lifespan_agent(self):
        expected = Mock()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(agent=expected))
        )

        self.assertIs(app_module.get_agent(request), expected)

    def test_create_agent_uses_all_toolsets(self):
        toolsets = [Mock(), Mock()]
        with patch.object(app_module, "Agent") as agent_class:
            result = app_module.create_agent(toolsets)

        self.assertIs(result, agent_class.return_value)
        self.assertEqual(agent_class.call_args.kwargs["toolsets"], toolsets)

    def test_create_agent_instructions_reject_tool_substitution_for_missing_inputs(self):
        with patch.object(app_module, "Agent") as agent_class:
            app_module.create_agent([])

        instructions = agent_class.call_args.kwargs["instructions"]
        self.assertIn(
            "필수 인자가 부족하다는 이유로 관련성이 낮은 다른 도구를 대신 선택하지 않는다.",
            instructions,
        )
        self.assertNotIn(
            "일부 항목만 처리된 상태에서는 최종 출력 도구를 호출하지 않는다.",
            instructions,
        )

    async def test_chat_logs_one_complete_event_with_final_decision(self):
        result = SimpleNamespace(
            output=SimpleNamespace(
                status="NOT_APPLICABLE",
                missing_parameter=[],
                answer="도구를 사용할 수 없습니다.",
            ),
            all_messages=Mock(return_value=[]),
        )

        runtime_agent = SimpleNamespace(run=AsyncMock(return_value=result))
        with (
            patch.object(app_module, "write_qa_audit_log"),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            await app_module.chat(
                ChatRequest(request="test"),
                runtime_agent=runtime_agent,
            )

        logs = "\n".join(captured.output)
        self.assertEqual(len(captured.records), 1)
        self.assertIn(
            "CHAT COMPLETE status=NOT_APPLICABLE tool_count=0 missing_parameter_count=0",
            logs,
        )

    async def test_unexpected_error_log_does_not_include_exception_message(self):
        runtime_agent = SimpleNamespace(
            run=AsyncMock(side_effect=RuntimeError("private exception detail"))
        )
        with (
            patch.object(app_module, "write_qa_audit_log"),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            response = await app_module.chat(
                ChatRequest(request="test"),
                runtime_agent=runtime_agent,
            )

        logs = "\n".join(captured.output)
        self.assertEqual(response.status, "FAILED")
        self.assertEqual(len(captured.records), 1)
        self.assertIn("CHAT COMPLETE status=FAILED", logs)
        self.assertIn("error_type=RuntimeError", logs)
        self.assertIn("error_location=app.py", logs)
        self.assertNotIn("private exception detail", logs)


if __name__ == "__main__":
    unittest.main()
