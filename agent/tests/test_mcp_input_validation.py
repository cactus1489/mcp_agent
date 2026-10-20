import unittest
from types import SimpleNamespace

from pydantic_ai import ModelRetry

from agent.src import mcp_harness as harness
# from agent.src.provenance import collect_api_calls, inject_provenance


class MCPInputValidationTests(unittest.IsolatedAsyncioTestCase):
    server = 'test-input-validation'

    def setUp(self):
        self.context = SimpleNamespace(run_id='test', tool_call_id='call', retry=0)
        self.schema = {
            'type': 'object',
            'properties': {
                'risk': {'type': 'string', 'enum': ['cold_risk', 'heat_risk']},
                'view': {'type': 'string', 'enum': ['list', 'detail']},
                'farm_code': {'type': 'string'},
            },
            'required': ['risk'],
            'allOf': [{
                'if': {'properties': {'view': {'const': 'list'}}, 'required': ['view']},
                'then': {'required': ['farm_code']},
            }],
        }

    def register(self, schema=None):
        harness.register_mcp_input_schemas(self.server, [
            SimpleNamespace(name='lookup', inputSchema=self.schema if schema is None else schema),
        ])

    async def run_tool(self, args, result=None):
        async def call_tool(name, received):
            self.remote_calls.append((name, received))
            return result if result is not None else {'status': 'success', 'code': 200, 'data': []}

        return await harness.process_mcp_tool_call(
            self.server, self.context, call_tool, 'lookup', args,
        )

    async def test_invalid_enum_is_rejected_before_remote_call(self):
        self.register()
        self.remote_calls = []
        with self.assertRaises(ModelRetry) as caught:
            await self.run_tool({'risk': '탄저병'})
        self.assertEqual(self.remote_calls, [])
        self.assertIn('risk', str(caught.exception))
        self.assertIn('cold_risk', str(caught.exception))

    async def test_missing_required_field_requests_correction(self):
        self.register()
        self.remote_calls = []
        with self.assertRaises(ModelRetry) as caught:
            await self.run_tool({})
        self.assertEqual(self.remote_calls, [])
        self.assertIn('risk', str(caught.exception))

    async def test_schema_conditional_required_field_is_enforced(self):
        self.register()
        self.remote_calls = []
        with self.assertRaises(ModelRetry) as caught:
            await self.run_tool({'risk': 'cold_risk', 'view': 'list'})
        self.assertEqual(self.remote_calls, [])
        self.assertIn('farm_code', str(caught.exception))

    async def test_valid_empty_response_is_success_without_retry(self):
        self.register()
        self.remote_calls = []
        result = await self.run_tool({'risk': 'cold_risk'})
        self.assertEqual(result, {'status': 'success', 'code': 200, 'data': []})
        self.assertEqual(len(self.remote_calls), 1)

    async def test_invalid_input_response_becomes_retry_with_masked_feedback(self):
        self.register({'type': 'object', 'properties': {'risk': {'type': 'string'}}})
        self.remote_calls = []
        with self.assertRaises(ModelRetry) as caught:
            await self.run_tool({'risk': '탄저병'}, {
                'status': 'error', 'code': 'INVALID_INPUT',
                'error': {'message': 'Only weather risks are supported', 'api_key': 'hidden-secret'},
            })
        self.assertEqual(len(self.remote_calls), 1)
        self.assertIn('Only weather risks are supported', str(caught.exception))
        self.assertNotIn('hidden-secret', str(caught.exception))

    async def test_auth_and_server_errors_are_not_input_retries(self):
        self.register()
        self.remote_calls = []
        for code in ('UNAUTHORIZED', 'SERVER_ERROR', 'NOT_FOUND'):
            with self.subTest(code=code):
                result = {'status': 'error', 'code': code}
                self.assertIs(await self.run_tool({'risk': 'cold_risk'}, result), result)

    async def test_second_invalid_response_returns_failure_without_aborting_run(self):
        self.register()
        self.remote_calls = []
        self.context.retry = 1
        result = {'status': 'error', 'code': 'INVALID_INPUT', 'message': 'Unsupported combination'}
        self.assertIs(await self.run_tool({'risk': 'cold_risk'}, result), result)

    async def test_second_schema_error_still_does_not_reach_server(self):
        self.register()
        self.remote_calls = []
        self.context.retry = 1
        result = await self.run_tool({'risk': 'wrong'})
        self.assertEqual(self.remote_calls, [])
        self.assertEqual(result['code'], 'INVALID_INPUT')

    async def test_reregistering_tools_clears_removed_schema(self):
        self.register()
        harness.register_mcp_input_schemas(self.server, [])
        self.remote_calls = []
        result = await self.run_tool({})
        self.assertEqual(result['status'], 'success')

    # 비활성화: provenance 후처리 전용 테스트를 복원을 위해 보존한다.
    # async def test_retry_success_replaces_failure_in_provenance(self):
    #     self.register()
    #     self.remote_calls = []
    #     with collect_api_calls() as calls:
    #         with self.assertRaises(ModelRetry):
    #             await self.run_tool({'risk': 'cold_risk'}, {'status': 'error', 'code': 'INVALID_INPUT'})
    #         self.context.retry = 1
    #         await self.run_tool({'risk': 'heat_risk'})
    #     self.assertNotIn('조회 실패', inject_provenance('result', calls))
