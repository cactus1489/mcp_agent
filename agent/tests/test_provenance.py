# 비활성화: 출처·시점 표기를 response_templates.py의 LLM 지침으로 이전함.
# 기존 구현은 복원을 위해 주석으로 보존한다.
# import unittest
# from datetime import datetime, timedelta, timezone
#
# from agent.src.provenance import collect_api_calls, inject_provenance, record_api_call
#
#
# class ProvenanceRetryTests(unittest.TestCase):
#     def render(self, statuses):
#         started = datetime(2026, 10, 15, tzinfo=timezone.utc)
#         with collect_api_calls() as calls:
#             for index, (server, tool, success) in enumerate(statuses):
#                 record_api_call(server, tool, {
#                     'status': 'success' if success else 'error',
#                     'code': 200 if success else 500,
#                 }, started + timedelta(minutes=index))
#         return inject_provenance('예찰 결과', calls)
#
#     def test_successful_retry_removes_failure_and_uses_final_time(self):
#         answer = self.render([
#             ('ncpms', 'search', False),
#             ('ncpms', 'search', True),
#         ])
#         self.assertNotIn('조회 실패', answer)
#         self.assertIn('09:01 기준', answer)
#         self.assertNotIn('09:00 기준', answer)
#
#     def test_failed_final_call_is_reported(self):
#         answer = self.render([
#             ('ncpms', 'search', True),
#             ('ncpms', 'search', False),
#         ])
#         self.assertIn('조회 실패', answer)
#         self.assertIn('09:01 기준', answer)
#         self.assertNotIn('09:00 기준', answer)
#
#     def test_other_tool_failure_is_preserved(self):
#         answer = self.render([
#             ('ncpms', 'other_search', False),
#             ('ncpms', 'search', True),
#         ])
#         self.assertIn('조회 실패', answer)
#
#     def test_other_server_failure_is_preserved(self):
#         answer = self.render([
#             ('early-warning', 'search', False),
#             ('ncpms', 'search', True),
#         ])
#         self.assertIn('농촌진흥청 농장맞춤형 기상재해 조기경보서비스: 조회 실패', answer)
#         self.assertNotIn('농촌진흥청 국가농작물병해충관리시스템: 조회 실패', answer)
#
#
# if __name__ == '__main__':
#     unittest.main()
