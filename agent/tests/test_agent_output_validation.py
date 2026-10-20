import unittest
from unittest.mock import patch

from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agent.src import app as app_module


class AgentOutputValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_without_mcp_result_requests_status_correction(self):
        requests = []

        def respond(messages, info):
            requests.append(messages)
            status = "SUCCESS" if len(requests) == 1 else "NOT_APPLICABLE"
            return ModelResponse(parts=[ToolCallPart(
                "return_agent_output",
                {"status": status, "answer": "요청을 처리할 수 있는 MCP 도구가 없습니다."},
            )])

        with patch.object(app_module, "model", FunctionModel(respond)):
            agent = app_module.create_agent([])
        result = await agent.run("하이")

        self.assertEqual(len(requests), 2)
        self.assertEqual(result.output.status, "NOT_APPLICABLE")
        retries = [part for message in requests[-1] for part in message.parts
                   if isinstance(part, RetryPromptPart)]
        self.assertTrue(any("NOT_APPLICABLE" in str(part.content) for part in retries))

    async def test_non_success_statuses_do_not_require_mcp_results(self):
        for status in ("NOT_APPLICABLE", "NEED_MORE_INFO", "FAILED"):
            with self.subTest(status=status):
                output = {"status": status, "answer": "안내"}
                if status == "NEED_MORE_INFO":
                    output["missing_input_tools"] = [
                        {"tool_name": "lookup", "missing_parameter": ["crop_name"]}
                    ]

                def respond(messages, info):
                    return ModelResponse(parts=[ToolCallPart("return_agent_output", output)])

                with patch.object(app_module, "model", FunctionModel(respond)):
                    agent = app_module.create_agent([])
                result = await agent.run("요청")
                self.assertEqual(result.output.status, status)

    async def test_success_with_empty_normal_mcp_result_is_preserved(self):
        calls = 0

        def respond(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("lookup", {})])
            return ModelResponse(parts=[ToolCallPart(
                "return_agent_output", {"status": "SUCCESS", "answer": "자료가 없습니다."}
            )])

        with patch.object(app_module, "model", FunctionModel(respond)):
            agent = app_module.create_agent([])

        @agent.tool_plain
        def lookup() -> dict:
            return {"status": "success", "code": 200, "data": []}

        result = await agent.run("조회")
        self.assertEqual(result.output.status, "SUCCESS")
        self.assertEqual(calls, 2)
