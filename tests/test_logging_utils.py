import sys
import unittest
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from agent.logging_utils import (
    log_chat_complete,
    log_mcp_server_connected,
    log_mcp_server_connection_failed,
    log_mcp_tool_complete,
    log_mcp_tool_start,
)


class LoggingUtilsTests(unittest.TestCase):
    def test_tool_complete_log_contains_only_execution_metadata(self):
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_mcp_tool_complete(
                "trace-123",
                "agriculture",
                "sample",
                "call-456",
                1,
                0.25,
            )

        logs = "\n".join(captured.output)
        self.assertIn("MCP TOOL CALL COMPLETE", logs)
        self.assertIn("trace-123", logs)
        self.assertIn("server=agriculture", logs)
        self.assertIn("tool=sample", logs)
        self.assertIn("call_id=call-456", logs)
        self.assertIn("attempt=1", logs)
        self.assertIn("duration=0.250s", logs)
        for removed_field in ("status=", "code=", "data_type=", "result_type=", "item_count="):
            self.assertNotIn(removed_field, logs)

    def test_chat_complete_logs_status_counts_and_elapsed_once(self):
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            log_chat_complete("trace-123", "SUCCESS", 2, 0, 1.25)

        logs = "\n".join(captured.output)
        self.assertEqual(len(captured.records), 1)
        self.assertIn("trace-123", logs)
        self.assertIn("CHAT COMPLETE", logs)
        self.assertIn("status=SUCCESS", logs)
        self.assertIn("tool_count=2", logs)
        self.assertIn("missing_parameter_count=0", logs)
        self.assertIn("elapsed=1.250s", logs)

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

    def test_chat_complete_failure_logs_location_and_mcp_context_once(self):
        error = None
        try:
            raise RuntimeError("private detail")
        except RuntimeError as exc:
            exc.mcp_server_id = "agriculture"
            exc.mcp_tool_name = "sample"
            exc.code = 503
            error = exc

        with self.assertLogs("uvicorn.error", level="ERROR") as captured:
            log_chat_complete("trace-123", "FAILED", 0, 0, 1.25, error)

        logs = "\n".join(captured.output)
        self.assertEqual(len(captured.records), 1)
        self.assertIn("CHAT COMPLETE status=FAILED", logs)
        self.assertIn("error_type=RuntimeError", logs)
        self.assertIn("error_location=test_logging_utils.py", logs)
        self.assertIn("server=agriculture", logs)
        self.assertIn("tool=sample", logs)
        self.assertIn("code=503", logs)
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
