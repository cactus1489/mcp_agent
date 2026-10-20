# 비활성화: 출처·시점 표기를 response_templates.py의 LLM 지침으로 이전함.
# 기존 구현은 복원을 위해 주석으로 보존한다.
# import re
# from collections.abc import Iterator, Mapping
# from contextlib import contextmanager
# from contextvars import ContextVar
# from dataclasses import dataclass
# from datetime import datetime, timedelta, timezone
# from typing import Any
#
# KST = timezone(timedelta(hours=9), name='KST')
#
# SOURCE_NAMES = {
#     'cultivation': '농촌진흥청 농사로',
#     'fruit': '농촌진흥청 농사로',
#     'food': '농촌진흥청 농사로',
#     'greenhouse': '농촌진흥청 농사로',
#     'nongsaro': '농촌진흥청 농사로',
#     'ncpms': '농촌진흥청 국가농작물병해충관리시스템',
#     'pesticide': '농촌진흥청 농약안전정보시스템',
#     'soil': '농촌진흥청 흙토람',
#     'agricultural-weather': '농촌진흥청 농업기상정보서비스',
#     'early-warning': '농촌진흥청 농장맞춤형 기상재해 조기경보서비스',
#     'young-farmer': '농촌진흥청 똑똑청년농부',
#     'kma-short-term-forecast': '기상청 단기예보',
# }
# OBSERVATION_KEYS = ('observed_at', 'observation_time', 'observed_time',
#                     'measured_at', 'measurement_time', 'obs_time', 'obsrDt', '실측시간', '관측시각')
# OBSERVATION_TYPES = {'observation', 'observations', 'observed', 'measurement', 'realtime', '관측', '실측'}
# FORECAST_TYPES = {'forecast', 'prediction', '예보', '예측'}
#
#
# @dataclass(frozen=True)
# class ApiCall:
#     server_id: str
#     tool_name: str
#     called_at: datetime
#     sources: tuple[str, ...]
#     observation: bool
#     observed_times: tuple[str, ...]
#     success: bool
#
#
# _CALLS: ContextVar[list[ApiCall] | None] = ContextVar('api_provenance_calls', default=None)
#
#
# @contextmanager
# def collect_api_calls() -> Iterator[list[ApiCall]]:
#     calls: list[ApiCall] = []
#     token = _CALLS.set(calls)
#     try:
#         yield calls
#     finally:
#         _CALLS.reset(token)
#
#
# def _text(value: Any) -> str:
#     return ' '.join(value.split()) if isinstance(value, str) else ''
#
#
# def _metadata(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
#     containers = [result]
#     for key in ('metadata', 'meta', 'source'):
#         if isinstance(result.get(key), Mapping):
#             containers.append(result[key])
#     data = result.get('data')
#     if isinstance(data, Mapping):
#         containers.append(data)
#         for key in ('metadata', 'meta'):
#             if isinstance(data.get(key), Mapping):
#                 containers.append(data[key])
#     return containers
#
#
# def _source_name(value: Any) -> str:
#     if isinstance(value, Mapping):
#         for key in ('api_id', 'system_id', 'server_id'):
#             identifier = _text(value.get(key))
#             if identifier in SOURCE_NAMES:
#                 return SOURCE_NAMES[identifier]
#         for key in ('system_name', 'name', 'official_name'):
#             if _text(value.get(key)):
#                 return _source_name(value[key])
#         return _source_name(' '.join(filter(None, (_text(value.get('provider')), _text(value.get('dataset'))))))
#     name = _text(value)
#     if name in SOURCE_NAMES:
#         return SOURCE_NAMES[name]
#     aliases = {'농사로': 'nongsaro', 'nongsaro.go.kr': 'nongsaro',
#                '농약안전정보시스템': 'pesticide', 'psis.rda.go.kr': 'pesticide',
#                '국가농작물병해충관리시스템': 'ncpms', 'ncpms.rda.go.kr': 'ncpms',
#                '흙토람': 'soil', 'soil.rda.go.kr': 'soil', '기상청 단기예보': 'kma-short-term-forecast',
#                '농업기상 상세 관측데이터': 'agricultural-weather', '똑똑청년농부': 'young-farmer'}
#     for alias, identifier in aliases.items():
#         if alias in name:
#             return SOURCE_NAMES[identifier]
#     return '' if name.startswith(('http://', 'https://')) else name
#
#
# def _sources(server_id: str, metadata: list[Mapping[str, Any]]) -> tuple[str, ...]:
#     names: list[str] = []
#     for container in metadata:
#         for key in ('api_id', 'system_id'):
#             identifier = _text(container.get(key))
#             if identifier in SOURCE_NAMES:
#                 names.append(SOURCE_NAMES[identifier])
#         for key in ('sources', 'source', 'system_name'):
#             raw = container.get(key)
#             for value in raw if isinstance(raw, list) else [raw]:
#                 name = _source_name(value)
#                 if name:
#                     names.append(name)
#     api_names = [SOURCE_NAMES[_text(m[key])] for m in metadata
#                  for key in ('api_id', 'system_id') if _text(m.get(key)) in SOURCE_NAMES]
#     if api_names:
#         return tuple(dict.fromkeys(api_names))
#     if not names or names == ['농촌진흥청']:
#         names = [SOURCE_NAMES.get(server_id, f'{server_id} (공식 명칭 미등록)')]
#     return tuple(dict.fromkeys(names))
#
#
# def _format_time(value: Any) -> str:
#     raw = _text(value)
#     if not raw:
#         return ''
#     try:
#         parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
#     except ValueError:
#         parsed = None
#         for pattern in ('%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y%m%d', '%Y.%m.%d %H:%M'):
#             try:
#                 parsed = datetime.strptime(raw, pattern)
#                 break
#             except ValueError:
#                 continue
#     if parsed is None:
#         return raw
#     if (len(raw) == 8 and raw.isdigit()) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
#         return parsed.strftime('%Y년 %m월 %d일')
#     if parsed.tzinfo is not None:
#         return parsed.astimezone(KST).strftime('%Y년 %m월 %d일 %H:%M:%S KST')
#     return parsed.strftime('%Y년 %m월 %d일 %H:%M:%S') + ' (시간대 미제공)'
#
#
# def _observed_times(value: Any, depth: int = 0) -> list[str]:
#     if depth > 8:
#         return []
#     times: list[str] = []
#     if isinstance(value, Mapping):
#         for key in OBSERVATION_KEYS:
#             stamp = _format_time(value.get(key))
#             if stamp:
#                 times.append(stamp)
#         obs_date, obs_time = _text(value.get('obsrDate')), _text(value.get('obsrTime'))
#         if obs_date and obs_time:
#             times.append(_format_time(obs_date + obs_time))
#         for key, child in value.items():
#             if key not in OBSERVATION_KEYS and isinstance(child, (Mapping, list)):
#                 times.extend(_observed_times(child, depth + 1))
#     elif isinstance(value, list):
#         for child in value:
#             times.extend(_observed_times(child, depth + 1))
#     return list(dict.fromkeys(times))
#
#
# def record_api_call(server_id: str, tool_name: str, result: Mapping[str, Any], called_at: datetime) -> None:
#     calls = _CALLS.get()
#     if calls is None:
#         return
#     metadata = _metadata(result)
#     kinds = {_text(m.get(key)).lower() for m in metadata for key in ('data_type', 'data_kind', 'type')}
#     times = _observed_times(result)
#     forecast = bool(kinds & FORECAST_TYPES) or 'forecast' in tool_name.lower()
#     if kinds & OBSERVATION_TYPES and not forecast:
#         for container in metadata:
#             stamp = _format_time(container.get('timestamp'))
#             if stamp and stamp not in times:
#                 times.append(stamp)
#     observation = not forecast and (bool(kinds & OBSERVATION_TYPES) or bool(times)
#                                    or 'observation' in tool_name.lower())
#     calls.append(ApiCall(server_id, tool_name, called_at, _sources(server_id, metadata),
#                          observation, tuple(times), result.get('status') == 'success' and str(result.get('code')) == '200'))
#
#
# def _display_time(stamp: str) -> str:
#     return re.sub(r'(\d{2}:\d{2}):\d{2}', r'\1', stamp).replace(' KST', '')
#
#
# def inject_provenance(answer: str, calls: list[ApiCall]) -> str:
#     if not calls:
#         return answer
#     # Use the final returned result for each server/tool, including retries.
#     latest_calls: dict[tuple[str, str], ApiCall] = {}
#     for call in calls:
#         latest_calls[(call.server_id, call.tool_name)] = call
#     calls = list(latest_calls.values())
#
#     heading = re.search(r'(?m)^\s*(?:#{1,6}\s*)?(?:\*\*)?확인 및 안내사항(?:\*\*)?\s*$', answer)
#     body, old_footer = (answer[:heading.start()].rstrip(), answer[heading.end():]) if heading else (answer.rstrip(), '')
#     caution_pattern = r'(?m)^\s*(?:[-*]\s+)?(?:#{1,6}\s*)?(?:\*\*)?주의사항\s*:'
#     caution = re.search(caution_pattern, old_footer)
#     caution_text = old_footer[caution.start():].strip() if caution else ''
#     body_caution = re.search(caution_pattern, body)
#     if body_caution:
#         # Existing policy puts cautions last; move this block below metadata.
#         caution_text = body[body_caution.start():].strip()
#         body = body[:body_caution.start()].rstrip()
#     sources = list(dict.fromkeys(source for call in calls for source in call.sources))
#     references: list[tuple[str, str]] = []
#     notices: list[str] = []
#     for call in calls:
#         label = ', '.join(call.sources)
#         called = _display_time(_format_time(call.called_at.isoformat()))
#         if call.success and call.observation and call.observed_times:
#             times = dict.fromkeys(_display_time(stamp) for stamp in call.observed_times)
#             reference = ', '.join(times) + ' 기준 (실측)'
#         else:
#             reference = called + ' 기준'
#             if not call.success:
#                 notices.append(f'{label}: 조회 실패')
#             elif call.observation:
#                 notices.append(f'{label}: 실측 시점 미제공')
#         if (label, reference) not in references:
#             references.append((label, reference))
#     footer = '### 확인 및 안내사항\n\n정보 출처: ' + ', '.join(sources)
#     unique_times = list(dict.fromkeys(reference for _, reference in references))
#     if len(unique_times) == 1:
#         footer += '\n\n기준 시점: ' + unique_times[0]
#     else:
#         footer += '\n\n기준 시점:\n' + '\n'.join(
#             f'- {label}: {reference}' for label, reference in references
#         )
#     if notices:
#         footer += '\n\n' + '\n'.join('- ' + notice for notice in dict.fromkeys(notices))
#     if caution_text:
#         footer += '\n\n' + caution_text
#     return body + '\n\n' + footer if body else footer
