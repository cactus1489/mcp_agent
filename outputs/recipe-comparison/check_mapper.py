import importlib.util
import json
import sys
from pathlib import Path

source = Path('D:/dev/workspace/isaki-mcp-dev/src/mcp_server/domain/food/standard/model.py')
spec = importlib.util.spec_from_file_location('recipe_mapping_check', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

steps = [{'ordr': '1', 'ckry_Info': '재료를 씻는다.'},
         {'ordr': '2', 'ckry_Info': '재료를 익힌다.'}]
results = []
for label, value, expected in [
    ('JSON list', steps, 2),
    ('XML wrapper', {'ckry': steps}, 2),
    ('single step wrapper', {'ckry': steps[0]}, 1),
    ('empty list', [], 0),
    ('null list', None, 0),
]:
    item = module.FoodRecipeItem.model_validate({'fd_Nm': '형식 검증용 샘플', 'ckry_List': value})
    assert len(item.cooking_steps) == expected
    if expected:
        assert item.cooking_steps[0].order == 1
        assert item.cooking_steps[0].description == '재료를 씻는다.'
    results.append({'format': label, 'expected_steps': expected, 'actual_steps': len(item.cooking_steps)})
Path(__file__).with_name('mapper_checks.json').write_text(
    json.dumps({'source': str(source), 'synthetic_inputs_only': True, 'checks': results}, ensure_ascii=False, indent=2),
    encoding='utf-8',
)
print(json.dumps({'checks': results, 'upstream_comparison_completed': False}))
