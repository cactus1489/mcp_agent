import asyncio
import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from agent.config import settings
from agent.src.app import app
SOURCE = Path('D:/outputs/agri-test-cases/농업에이전트_유형별_테스트질문_37개.csv')


async def main():
    raw = SOURCE.read_bytes()
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'cp949'
    cases = list(csv.DictReader(io.StringIO(raw.decode(encoding))))
    result_path = HERE / 'responses.json'
    if result_path.exists():
        raise RuntimeError('Existing results found; refusing to repeat paid requests')
    run = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'source': str(SOURCE), 'encoding': encoding,
        'base_url': 'http://current-code-test',
        'execution_mode': 'ASGITransport with application lifespan; current code; real LLM and MCP',
        'excluded_old_server_requests': 2,
        'case_count': len(cases), 'concurrency': 2,
        'history_supported': False, 'cases': [],
    }
    (HERE / 'input_snapshot.csv').write_bytes(raw)
    semaphore = asyncio.Semaphore(2)

    def checkpoint():
        result_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')

    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=run['base_url'],
        headers={'Authorization': settings.agent_api_key}, timeout=205, trust_env=False,
    ) as client:
        health = await client.get('/openapi.json', timeout=10)
        health.raise_for_status()
        for case in cases:
            run['cases'].append({'input': case, 'turns': []})
        checkpoint()

        async def execute(case):
            question = case['input']['테스트 질문']
            turns = [part.strip() for part in re.split(r'[①②]', question) if part.strip()]
            if len(turns) != 2:
                turns = [question]
            for index, text in enumerate(turns, 1):
                async with semaphore:
                    started = time.monotonic()
                    item = {'turn': index, 'request': text}
                    try:
                        response = await client.post('/chat', json={'request': text})
                        item['http_status'] = response.status_code
                        try:
                            item['response'] = response.json()
                        except ValueError:
                            item['response_text'] = response.text[:10000]
                    except httpx.HTTPError as exc:
                        item['client_error'] = type(exc).__name__
                    item['elapsed_seconds'] = round(time.monotonic() - started, 3)
                    case['turns'].append(item)
                    checkpoint()
                    body = item.get('response', {})
                    print(json.dumps({
                        'case': case['input']['번호'], 'turn': index,
                        'http': item.get('http_status'), 'status': body.get('status'),
                        'tools': [t['tool_name'] for t in body.get('tool_results', [])],
                        'seconds': item['elapsed_seconds'], 'error': item.get('client_error'),
                    }, ensure_ascii=True), flush=True)

        await asyncio.gather(*(execute(case) for case in run['cases']))
    run['finished_at'] = datetime.now(timezone.utc).isoformat()
    checkpoint()
    print('All cases recorded', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
