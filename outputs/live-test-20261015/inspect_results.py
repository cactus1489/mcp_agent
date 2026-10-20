import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
run = json.loads(Path(__file__).with_name('responses.json').read_text(encoding='utf-8'))
ids = set(sys.argv[1:])

def short(value, depth=0):
    if depth > 5:
        return str(value)[:300]
    if isinstance(value, dict):
        return {k: short(v, depth + 1) for k, v in value.items()
                if k not in ('contents', 'content', 'description', 'information_url', 'image', 'image_url')}
    if isinstance(value, list):
        return {'count': len(value), 'sample': [short(v, depth + 1) for v in value[:3]]}
    return value[:400] if isinstance(value, str) else value

for case in run['cases']:
    if ids and case['input']['번호'] not in ids:
        continue
    print('CASE', case['input']['번호'], case['input']['분야'])
    for turn in case['turns']:
        body = turn.get('response', {})
        print('TURN', turn['turn'], 'STATUS', body.get('status'), 'TIME', turn['elapsed_seconds'])
        print(body.get('answer', turn.get('client_error', '')))
        print('MISSING', json.dumps(body.get('missing_input_tools'), ensure_ascii=False))
        print('TOOLS', json.dumps([{'name': tool['tool_name'], 'data': short(tool['data'])}
                                   for tool in body.get('tool_results', [])], ensure_ascii=False))
