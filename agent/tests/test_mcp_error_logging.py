import unittest
from types import SimpleNamespace

from agent.src.mcp_harness import process_mcp_tool_call


class MCPErrorLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, result=None, error=None):
        async def call_tool(name, args):
            if error is not None:
                raise error
            return result

        return await process_mcp_tool_call(
            'early-warning',
            SimpleNamespace(run_id='trace', tool_call_id='call', retry=0),
            call_tool, 'get_early_warning_advice', {},
        )

    async def test_failed_response_logs_status_code_and_masked_error(self):
        result = {
            'status': 'error', 'code': 400, 'message': '지원하지 않는 위험 항목',
            'error': {'detail': 'invalid risk', 'api_key': 'secret-key'},
            'data': {'private_payload': 'do-not-log'},
        }
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            returned = await self.call(result)
        output = '\n'.join(logs.output)
        self.assertIs(returned, result)
        for value in ('MCP TOOL CALL FAILED', 'early-warning', 'get_early_warning_advice',
                      'trace', 'call', '400', '지원하지 않는 위험 항목', 'invalid risk', '***'):
            self.assertIn(value, output)
        self.assertNotIn('secret-key', output)
        self.assertNotIn('do-not-log', output)

    async def test_success_does_not_log_failure(self):
        with self.assertNoLogs('uvicorn.error', level='WARNING'):
            result = await self.call({'status': 'success', 'code': '200'})
        self.assertEqual(result['status'], 'success')

    async def test_exception_is_logged_and_reraised(self):
        error = RuntimeError('upstream unavailable')
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            with self.assertRaises(RuntimeError) as caught:
                await self.call(error=error)
        self.assertIs(caught.exception, error)
        self.assertEqual(error.mcp_server_id, 'early-warning')
        output = '\n'.join(logs.output)
        self.assertIn('MCP TOOL CALL FAILED', output)
        self.assertIn('RuntimeError', output)
        self.assertIn('upstream unavailable', output)
