import csv
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = Path('D:/outputs/agri-test-cases/농업에이전트_유형별_테스트질문_37개.csv')

if sys.argv[1:] == ['save']:
    meta = json.loads((HERE / 'metadata.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == meta['sha256'], 'Source changed during edit'
    candidate = (HERE / 'updated.csv').read_bytes()
    result = list(csv.DictReader(io.StringIO(candidate.decode('utf-8-sig'))))
    before = json.loads((HERE / 'before.json').read_text(encoding='utf-8'))
    assert len(result) == 25 and result[:20] == before
    assert [r['번호'] for r in result] == [str(i) for i in range(1, 26)]
    assert len({r['테스트 질문'] for r in result[20:]}) == 5
    assert all(r['유형'] == '여러 정보 통합 조회' and r['기대 상태'] == 'SUCCESS' for r in result[20:])
    assert not any('\ufffd' in str(r) for r in result)
    backup = SOURCE.with_name(SOURCE.stem + '_추가전_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv.bak')
    with backup.open('xb') as stream:
        stream.write(SOURCE.read_bytes())
    SOURCE.write_bytes(candidate)
    assert SOURCE.read_bytes() == candidate
    print(json.dumps({'saved': True, 'preserved_rows': 20, 'added_rows': 5, 'total': 25, 'columns': len(result[0]), 'backup': str(backup)}, ensure_ascii=True))
    sys.exit(0)

raw = SOURCE.read_bytes()
enc = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'cp949'
reader = csv.DictReader(io.StringIO(raw.decode(enc)))
headers = reader.fieldnames
before = list(reader)
assert len(before) == 20
assert headers == ['번호', '유형', '테스트 질문', '기대 상태', '기대 결과·행동']
(HERE / 'before.json').write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
(HERE / 'metadata.json').write_text(json.dumps({'sha256': hashlib.sha256(raw).hexdigest()}), encoding='utf-8')

normal = '시험 조건: 새 대화에서 실행하고 관련 도구가 정상 응답하며 전체 요청이 제한시간 내 완료되도록 한다. 정상 빈 결과도 SUCCESS다. '
policy = ' 일부 도구만 실패해도 정상 처리된 도구가 하나 이상 있으면 SUCCESS와 미확인 항목을 함께 안내한다. 수치·조건·출처·자료 기준 시점은 원문과 대조한다.'
additions = [
    (
        '경기도 양평군 강상면 강남로 795-23에서 사과를 재배한다고 가정하고, 오늘 평균풍속과 최고풍속 예측값, 사과 풍해 위험도, 풍해의 사전·즉시·사후 대응지침을 각각 조회해 줘. 예측값과 위험도, 대응지침의 기준 시점과 단위를 구분해 줘.',
        normal + '날씨·작목별 풍해 위험도·대응지침을 각각 조회한다. 제공된 주소·사과·풍해 조건을 유지하고 풍속 단위와 지침의 대응 시점·생육 단계를 보존한다. 풍속만으로 임의 위험등급을 만들거나 풍해를 다른 재해로 대체하지 않는다. 주소는 가상의 사과 재배 조건을 시험하는 위치이며 실제 사과 농장이라고 단정하지 않는다.' + policy,
    ),
    (
        '경기도 양평에 있는 단동 온실을 가정할게. 폭 8m, 길이 50m, 측고 3m, 동고 5m, 연동수 1동 조건에서 설정온도 18도와 20도의 예상 난방부하·연료 소비량을 각각 계산해 비교해 줘. 경기도 양평군 강상면 강남로 795-23의 오늘 최저기온 예측값도 별도로 조회해 줘. 나머지 계산 조건은 도구에 명시된 기본값을 적용하고 어떤 값을 사용했는지 밝혀 줘.',
        normal + '온도만 달리한 동일 온실 조건으로 에너지 계산을 각각 수행하고 최저기온 예측은 별도 조회한다. 폭·길이·연동수·높이를 실제 인자에 반영하고 연료·피복·난방 방식·산정 기간 등 적용 기본값을 표시한다. 부하 단위와 연료량 단위를 구분한다. 오늘 최저기온이 에너지 계산의 입력에 포함되지 않았다면 두 결과를 직접 연결해 계산한 것처럼 설명하지 않는다.' + policy,
    ),
    (
        '2024년과 2025년 경상북도 안동시의 사과·배 재배면적을 각각 조회해 연도별 변화를 비교해 줘. 경북 청년농업인이 참고할 수 있는 스마트팜 교육 공고도 찾아 교육기관과 신청기간을 함께 정리해 줘. 통계와 교육 공고는 구분해서 보여 줘.',
        normal + '재배면적 도구로 두 과수·두 연도 자료를 확보하고 교육 공고는 별도 도구로 조회한다. 같은 지역·품목·통계 구분·단위인 자료끼리만 증감을 계산한다. 한 연도 자료가 없으면 0으로 채우거나 다른 지역·연도로 대체하지 않는다. 통계 연도와 공고 신청기간·조회 시각을 구분하고 청년 대상 및 접수 상태를 확인한다.' + policy,
    ),
    (
        '비빔밥 관련 메뉴의 재료·중량·알레르기 정보와 제공되는 조리 순서를 조회해 줘. 이와 별도로 당근 생것의 열량·탄수화물·식이섬유 함량도 실제 식품명, 기준량, 단위와 함께 알려 줘. 검색된 메뉴가 여러 개이면 메뉴별로 나누어 정리해 줘.',
        normal + '음식 레시피와 표준식품 영양 도구를 각각 조회한다. 메뉴별 재료·중량·알레르기를 보존하고 조리 순서가 실제로 있을 때만 안내한다. 조리 단계가 없으면 이번 조회에서 미제공이라고 표시한다. 일반 생당근과 가공품·다른 식품을 구분하고 영양값의 기준량·단위가 없으면 추정하지 않는다. 당근 성분값을 비빔밥 전체의 영양값으로 표현하지 않는다.' + policy,
    ),
    (
        '전남 지역 청년농업인을 위한 스마트팜 지원사업 공고와 교육 공고를 각각 조회해 줘. 같은 주제의 기술우수사례도 함께 찾아 사업명·신청기간·지원대상, 교육명·기관·일정, 사례명·주요 내용을 세 부분으로 나누어 정리해 줘. 모집이 끝난 공고는 종료됐다고 표시해 줘.',
        normal + '지원사업·교육·사례 도구를 모두 선택하고 사례 분류는 기술우수사례로 지정한다. 전남·스마트팜 조건을 유지하고 검색어가 맞지 않아 빈 결과여도 분류·지역을 근거 없이 바꾸지 않는다. 공고 대상과 신청기간·교육 일정을 구분하고 현재 접수 상태를 확인한다. 사례가 지원사업 선정·소득 증가를 보장한다고 해석하지 않는다.' + policy,
    ),
]
questions = {row['테스트 질문'] for row in before}
new = []
for number, (question, expectation) in enumerate(additions, 21):
    assert question not in questions
    new.append(dict(zip(headers, [str(number), '여러 정보 통합 조회', question, 'SUCCESS', expectation])))
rows = [headers] + [[row[key] for key in headers] for row in before + new]
assert all(len(row) == 5 and all(str(v).strip() for v in row) for row in rows)
(HERE / 'rows.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'preserved': len(before), 'added': len(new), 'total': len(rows)-1}))
