import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path('D:/dev/workspace/isaki-mcp-dev')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from agent.config import settings
from agent.src.mcp_harness import parse_mcp_server_configs
from pydantic_ai.mcp import MCPToolset


async def main():
    config = dotenv_values('D:/dev/workspace/AI_isaki_mcp/.env')
    # Official data.go.kr catalog 15143471 Swagger host, not the example local URL.
    base = 'https://apis.data.go.kr/1390803/AgriFood/FdFoodCkryImage1'
    key = config.get('NIHHS_API_KEY', '')
    if not base or not key:
        print('Upstream configuration unavailable')
        return
    endpoint = base.rstrip('/') + '/getKoreanFoodFdFoodCkryImageList1'
    params = {'serviceKey': key, 'page_No': '1', 'Page_Size': '3',
              'service_Type': 'json', 'food_Name': '비빔밥'}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(endpoint, params=params)
    except httpx.HTTPError as exc:
        print(json.dumps({'upstream_error': type(exc).__name__}))
        return
    if response.status_code != 200:
        print(json.dumps({'http_status': response.status_code}))
        return
    (HERE / 'upstream_raw_response.txt').write_bytes(response.content)
    text = response.content.decode('utf-8')
    try:
        payload = json.loads(text)
        repaired = False
    except json.JSONDecodeError:
        # Execute only the existing JSON repair function, without importing service setup.
        tree = ast.parse((ROOT / 'src/mcp_server/domain/food/standard/service.py').read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if
                 isinstance(node, ast.FunctionDef) and node.name == '_repair_food_recipe_json'
                 or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_JSON_CONTROL_ESCAPES' for t in node.targets)]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<existing-json-repair>', 'exec'), scope)
        text, controls, commas = scope['_repair_food_recipe_json'](text)
        payload = json.loads(text)
        repaired = {'control_characters': controls, 'duplicate_commas': commas}
    (HERE / 'upstream_response.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('recipe_model_for_comparison', ROOT / 'src/mcp_server/domain/food/standard/model.py')
    model = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = model
    spec.loader.exec_module(model)
    original_root = payload.get('response', payload)
    converted = model.FoodRecipeApiResponse.model_validate(original_root)
    normalized = converted.model_dump()
    (HERE / 'local_conversion.json').write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
    c = next(c for c in parse_mcp_server_configs() if c.id == 'food')
    async with MCPToolset(c.url, headers={'Authorization': settings.mcp_api_key}, init_timeout=10, read_timeout=20) as server:
        remote = await server.direct_call_tool('search_korean_food_recipes', {'food_Name': '비빔밥'})
    (HERE / 'mcp_response.json').write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding='utf-8')
    items = original_root.get('list')
    if items is None:
        items = (original_root.get('body') or {}).get('items', [])
        if isinstance(items, dict):
            items = items.get('item', [])
    if isinstance(items, dict):
        items = [items]
    summary = {
        'http_status': response.status_code,
        'official_spec': 'https://www.data.go.kr/data/15143471/openapi.do',
        'upstream_endpoint': endpoint,
        'json_repair': repaired,
        'upstream_keys': list(original_root),
        'upstream_items': [{'name': item.get('fd_Nm'), 'keys': list(item),
                            'cooking_fields': {k: v for k, v in item.items() if any(s in k.lower() for s in ('ckry', 'cook'))}}
                           for item in items],
        'local_conversion': [{'name': item.food_name, 'cooking_name': item.cooking_name,
                              'cooking_summary': item.cooking_summary,
                              'cooking_steps': [s.model_dump() for s in item.cooking_steps]}
                             for item in converted.body.items],
        'remote_conversion': [{'name': item.get('food_name'), 'cooking_name': item.get('cooking_name'),
                               'cooking_summary': item.get('cooking_summary'), 'cooking_steps': item.get('cooking_steps')}
                              for item in remote.get('data', {}).get('items', [])],
    }
    assert len(summary['upstream_items']) == len(summary['local_conversion']) == len(summary['remote_conversion']) == 3
    assert all('ckry_List' not in item['keys'] for item in summary['upstream_items'])
    assert all(item['cooking_fields'] == {'ckry_Nm': 'null', 'ckry_Sumry_Info': 'null'} for item in summary['upstream_items'])
    assert summary['local_conversion'] == summary['remote_conversion']
    (HERE / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
