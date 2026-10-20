import csv
import json
from html.parser import HTMLParser
from pathlib import Path

root = Path(__file__).resolve().parent
rows = list(csv.DictReader((root / '테스트결과.csv').open(encoding='utf-8-sig', newline='')))
run = json.loads((root / 'responses.json').read_text(encoding='utf-8'))
assert len(rows) == 18 and len(rows[0]) == 13
assert len({row['번호'] for row in rows}) == 18
assert sum(len(case['turns']) for case in run['cases']) == 21
text = (root / '테스트상세보고서.html').read_text(encoding='utf-8')
HTMLParser().feed(text)
assert all(f'id="case-{index}"' in text for index in range(1, 19))
assert '\ufffd' not in text
items = run['cases'][6]['turns'][0]['response']['tool_results'][-1]['data']['items']
values = [(n['irdnt_Nm'], n['cont_Info'], n['irdnt_Unit_Nm'])
          for item in items for group in item.get('irdnt', [])
          for n in group.get('irdnttcket', []) if n.get('irdnt_Nm') in ('에너지', '비타민 C')]
assert ('에너지', '19.0', '㎉') in values
assert ('비타민 C', '14.16', '㎎') in values
print(json.dumps({'verified_records': len(rows), 'requests': 21, 'nutrients_verified': True,
                  'html_bytes': (root / '테스트상세보고서.html').stat().st_size}))
