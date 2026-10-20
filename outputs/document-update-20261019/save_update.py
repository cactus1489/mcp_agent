import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
metadata = json.loads((HERE / 'metadata.json').read_text(encoding='utf-8'))
source = Path(metadata['source'])
assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata['sha256_before'], 'Source changed during edit'
updated = (HERE / 'updated.csv').read_bytes()
assert updated.startswith(b'\xef\xbb\xbf')
rows = list(csv.DictReader(io.StringIO(updated.decode('utf-8-sig'))))
assert len(rows) == 20 and len(rows[0]) == 10
assert not any('\ufffd' in str(row) for row in rows)
assert rows[9]['기대 상태'] == 'SUCCESS'
assert all(row['기대 상태'] == 'FAILED' for row in rows[18:])
backup = source.with_name(source.stem + '_수정전_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv.bak')
with backup.open('xb') as stream:
    stream.write(source.read_bytes())
source.write_bytes(updated)
assert source.read_bytes() == updated
metadata.update({'backup': str(backup), 'source_updated': True, 'encoding': 'UTF-8 BOM'})
(HERE / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'source_updated': True, 'cases': len(rows), 'columns': len(rows[0]), 'backup': str(backup)}, ensure_ascii=True))
