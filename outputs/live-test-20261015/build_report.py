import html
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
run = json.loads((HERE / 'responses.json').read_text(encoding='utf-8'))
reviews = json.loads((HERE / 'reviews.json').read_text(encoding='utf-8'))
assert run.get('finished_at'), 'Requests still running'
assert len(run['cases']) == len(reviews) == 18
header = ['번호', '유형', '분야', '테스트 질문', '기대 상태', '실제 상태', '상태 비교',
          '내용 판정', '발견 사항·근거', '성공 도구 조회', '누락 입력', '소요 시간(초)', '실행 추적 ID']
rows = [header]
cards = []
e = html.escape
for case in run['cases']:
    source = case['input']
    review = reviews[source['번호']]
    bodies = [turn.get('response', {}) for turn in case['turns']]
    actual = ' → '.join(str(body.get('status', '응답 없음')) for body in bodies)
    tools = [tool['tool_name'] for body in bodies for tool in body.get('tool_results', [])]
    missing = [item for body in bodies for item in body.get('missing_input_tools', [])]
    ids = [body.get('execution_trace_id', '') for body in bodies]
    seconds = round(sum(turn['elapsed_seconds'] for turn in case['turns']), 2)
    rows.append([source['번호'], source['유형'], source['분야'], source['테스트 질문'],
                 source['기대 상태'], actual, review['status_check'], review['verdict'],
                 review['findings'], ' → '.join(tools), json.dumps(missing, ensure_ascii=False),
                 seconds, ' / '.join(ids)])
    turns = []
    for turn in case['turns']:
        body = turn.get('response', {})
        turns.append(f'<h3>{turn["turn"]}차 요청 · {e(str(body.get("status")))} · {turn["elapsed_seconds"]}초</h3>'
                     f'<p class="question">{e(turn["request"])}</p>'
                     f'<pre>{e(body.get("answer", json.dumps(turn, ensure_ascii=False)))}</pre>'
                     '<details><summary>실제 도구 반환값과 누락 입력 보기</summary>'
                     f'<pre>{e(json.dumps(body, ensure_ascii=False, indent=2))}</pre></details>')
    cards.append(f'<section id="case-{source["번호"]}"><h2>{source["번호"]}. {e(source["분야"])} '
                 f'<span class="badge">{e(review["verdict"])}</span></h2>'
                 f'<p><b>기대:</b> {e(source["기대 상태"])} / <b>실제:</b> {e(actual)}</p>'
                 f'<p><b>기대 행동:</b> {e(source["기대 결과·행동"])}</p>'
                 f'<p class="finding"><b>검토 결과:</b> {e(review["findings"])}</p>'
                 + ''.join(turns) + '</section>')
counts = Counter(r['verdict'] for r in reviews.values())
summary = {'source_rows': 18, 'requests': sum(len(c['turns']) for c in run['cases']),
           'verdict_counts': dict(counts), 'status_matches': sum(r['status_check'] == '일치' for r in reviews.values()),
           'started_at': run['started_at'], 'finished_at': run['finished_at'],
           'mode': run['execution_mode']}
(HERE / 'report_rows.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
(HERE / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
table = ''.join(f'<tr><td><a href="#case-{r[0]}">{r[0]}</a></td><td>{e(r[2])}</td><td>{e(r[5])}</td>'
                f'<td>{e(r[6])}</td><td>{e(r[7])}</td><td>{e(r[8])}</td></tr>' for r in rows[1:])
document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>농업 에이전트 실조회 테스트 결과</title>'
            '<style>body{font:16px/1.65 system-ui,sans-serif;margin:32px auto;max-width:1200px;padding:0 22px;color:#203043;background:#f6f8fa}'
            'section{background:white;border:1px solid #d5dde5;border-radius:10px;padding:24px;margin:24px 0}'
            'table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #d5dde5;padding:10px;text-align:left;vertical-align:top}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui,sans-serif;background:#f5f7fa;padding:16px}'
            '.finding{background:#fff2ce;padding:14px}.badge{font-size:15px;background:#e8eef6;padding:5px 10px;border-radius:5px}'
            'summary{cursor:pointer;color:#125b9a}.question{border-left:4px solid #378b7f;padding-left:12px}</style>'
            '<h1>농업 에이전트 실조회 테스트 결과</h1>'
            '<p>2026-10-15 · 입력 파일에 실제 저장된 18개 질문 · 연속 질문을 분리한 21회 요청</p>'
            '<p><b>실행 환경:</b> 작업 폴더의 최신 FastAPI 앱을 ASGITransport로 실행하고 실제 설정의 LLM 및 MCP를 호출했다. '
            '현재 실행 중인 로컬 서버의 시작 시각이 코드 수정 전이어서 해당 서버에 보낸 사전 요청 2회는 본 판정에서 제외했다. 기존 서버는 변경하지 않았다.</p>'
            '<p><b>판정 기준:</b> 충족=명시된 핵심 요구 및 확인한 반환값과 일치; 부분충족=핵심 조회는 수행했으나 일부 누락·과장·표기 오류; '
            '미충족=요구한 조회 단계 누락 또는 원문 의미 변경; 조건미충족=시험 전제인 대화 이력 전달 기능이 없음. '
            'SUCCESS 상태만으로 내용 통과로 판정하지 않았다. 외부 원천 데이터 자체의 정확성·전체성을 별도 인증한 결과는 아니다.</p>'
            f'<p><b>내용 판정 집계:</b> {e(" / ".join(f"{key} {value}건" for key,value in counts.items()))}</p>'
            '<table><thead><tr><th>번호</th><th>분야</th><th>실제 상태</th><th>상태 비교</th><th>내용 판정</th><th>발견 사항</th></tr></thead>'
            f'<tbody>{table}</tbody></table>' + ''.join(cards) + '</html>')
(HERE / '테스트상세보고서.html').write_text(document, encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True))
