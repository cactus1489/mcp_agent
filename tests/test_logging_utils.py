import sys
import unittest
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent.logging_utils import (
    log_agent_decision,
    log_chat_complete,
    log_chat_failure,
    log_mcp_server_connected,
    log_mcp_server_connection_failed,
    log_mcp_tool_result,
    log_mcp_tool_start,
)


class LoggingUtilsTests(unittest.TestCase):
    def test_tool_result_log_contains_summary_without_raw_data(self):
        result = {
            "status": "success",
            "code": "200",
            "source": "private source text",
            "data": {
                "result_type": "candidates",
                "items": [{"phone": "010-1234-5678", "name": "private"}],
            },
        }

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_mcp_tool_result("trace-123", "agriculture", "sample", "call-456", result)

        logs = "\n".join(captured.output)
        self.assertIn("trace-123", logs)
        self.assertIn("server=agriculture", logs)
        self.assertIn("tool=sample", logs)
        self.assertIn("call_id=call-456", logs)
        self.assertIn("status=success", logs)
        self.assertIn("code=200", logs)
        self.assertIn("data_type=dict", logs)
        self.assertIn("result_type=candidates", logs)
        self.assertIn("item_count=1", logs)
        self.assertNotIn("private source text", logs)
        self.assertNotIn("010-1234-5678", logs)
        self.assertNotIn('"name": "private"', logs)

    def test_agent_decision_log_contains_status_and_counts(self):
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_agent_decision("trace-123", "SUCCESS", 2, 0)

        logs = "\n".join(captured.output)
        self.assertIn("trace-123", logs)
        self.assertIn("AGENT DECISION", logs)
        self.assertIn("status=SUCCESS", logs)
        self.assertIn("tool_count=2", logs)
        self.assertIn("missing_parameter_count=0", logs)

    def test_chat_complete_logs(self):
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_chat_complete("trace-123", 1.25)

        logs = "\n".join(captured.output)
        self.assertIn("CHAT COMPLETE elapsed=1.250s", logs)

    def test_mcp_tool_start_masks_sensitive_arguments(self):
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_mcp_tool_start(
                "trace-123",
                "agriculture",
                "sample",
                "call-456",
                1,
                {"api_key": "secret", "nested": {"password": "private"}},
            )

        logs = "\n".join(captured.output)
        self.assertIn("server=agriculture", logs)
        self.assertIn('"api_key": "***"', logs)
        self.assertNotIn("secret", logs)
        self.assertNotIn("private", logs)

    def test_chat_failure_does_not_log_exception_message(self):
        with self.assertLogs("uvicorn.error", level="ERROR") as captured:
            log_chat_failure("trace-123", 1.25, RuntimeError("private detail"))

        logs = "\n".join(captured.output)
        self.assertIn("type=RuntimeError", logs)
        self.assertNotIn("private detail", logs)

    def test_connection_logs_sanitize_url_and_exception(self):
        url = (
            "https://user:password@[2001:db8::1]:8101/mcp"
            "?api_key=secret-query#private-fragment"
        )

        with self.assertLogs("uvicorn.error", level="INFO") as connected:
            log_mcp_server_connected("agriculture", url, 5)
        with self.assertLogs("uvicorn.error", level="ERROR") as failed:
            log_mcp_server_connection_failed(
                "agriculture",
                url,
                RuntimeError("secret exception detail"),
            )

        logs = "\n".join(connected.output + failed.output)
        self.assertIn("id=agriculture", logs)
        self.assertIn("host=[2001:db8::1]:8101", logs)
        self.assertIn("tool_count=5", logs)
        self.assertIn("error_type=RuntimeError", logs)
        for secret in (
            "user",
            "password",
            "api_key",
            "secret-query",
            "private-fragment",
            "secret exception detail",
        ):
            self.assertNotIn(secret, logs)


if __name__ == "__main__":
    unittest.main()
