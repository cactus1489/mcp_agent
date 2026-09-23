import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv" / "Lib" / "site-packages"))

from pydantic_ai.messages import ToolReturnPart

from agent.mcp_result_utils import get_mcp_results


class MCPResultUtilsTests(unittest.TestCase):
    def test_get_mcp_results_converts_tool_return_parts(self):
        messages = [
            SimpleNamespace(
                parts=[
                    ToolReturnPart(
                        tool_name="search_weather_observations",
                        tool_call_id="call-1",
                        content={
                            "data": {"temperature": 25},
                            "source": {
                                "provider": "농촌진흥청",
                                "dataset": "농업기상 관측자료",
                            },
                        },
                    )
                ]
            )
        ]

        self.assertEqual(
            get_mcp_results(messages),
            [
                {
                    "tool_name": "search_weather_observations",
                    "tool_call_id": "call-1",
                    "data": {"temperature": 25},
                    "source": {
                        "provider": "농촌진흥청",
                        "dataset": "농업기상 관측자료",
                    },
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
