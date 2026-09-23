import sys
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent import app as app_module
from agent.models import ChatRequest


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

        with (
            patch.object(app_module.agent, "run", AsyncMock(return_value=result)),
            patch.object(app_module, "write_qa_audit_log"),
        ):
            response = None
            caught = None
            try:
                response = await app_module.chat(ChatRequest(request="test"))
            except Exception as exc:
                caught = exc

        self.assertIsNone(caught)
        self.assertEqual(response.status, "FAILED")

    async def test_lifespan_registers_mcp_output_schemas(self):
        tools = [SimpleNamespace(name="sample", outputSchema={"type": "object"})]

        with (
            patch.object(app_module.mcp_toolset, "list_tools", AsyncMock(return_value=tools)),
            patch.object(app_module, "register_mcp_output_schemas") as register,
        ):
            async with app_module.lifespan(app_module.app):
                pass

        register.assert_called_once_with(tools)

    async def test_chat_logs_final_agent_decision(self):
        result = SimpleNamespace(
            output=SimpleNamespace(
                status="NOT_APPLICABLE",
                missing_parameter=[],
                answer="도구를 사용할 수 없습니다.",
            ),
            all_messages=Mock(return_value=[]),
        )

        with (
            patch.object(app_module.agent, "run", AsyncMock(return_value=result)),
            patch.object(app_module, "write_qa_audit_log"),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            await app_module.chat(ChatRequest(request="test"))

        logs = "\n".join(captured.output)
        self.assertIn(
            "AGENT DECISION status=NOT_APPLICABLE tool_count=0 missing_parameter_count=0",
            logs,
        )

    async def test_unexpected_error_log_does_not_include_exception_message(self):
        with (
            patch.object(
                app_module.agent,
                "run",
                AsyncMock(side_effect=RuntimeError("private exception detail")),
            ),
            patch.object(app_module, "write_qa_audit_log"),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            response = await app_module.chat(ChatRequest(request="test"))

        logs = "\n".join(captured.output)
        self.assertEqual(response.status, "FAILED")
        self.assertIn("type=RuntimeError", logs)
        self.assertIn(
            "AGENT DECISION status=FAILED tool_count=0 missing_parameter_count=0",
            logs,
        )
        self.assertNotIn("private exception detail", logs)


if __name__ == "__main__":
    unittest.main()
