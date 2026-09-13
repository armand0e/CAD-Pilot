"""Untrusted source launcher. Its output is validated in a separate process."""
import json
from pathlib import Path
import runpy
import sys

entry = Path(sys.argv[1])
sys.path.insert(0, str(entry.resolve().parent))
namespace = runpy.run_path(str(entry))
parts = namespace.get('parts')
if parts is None and 'result' in namespace:
    parts = {'Model': namespace['result']}
if not isinstance(parts, dict) or not parts:
    raise ValueError('Define parts = {"Name": shape_or_document_object, ...}, or result = shape')
rows = []
for index, (name, value) in enumerate(parts.items()):
    shape = value.Shape if hasattr(value, 'Shape') else value
    file = f'part-{index}.brep'
    shape.exportBrep(file)
    rows.append({'name': str(name), 'file': file})
Path('build-parts.json').write_text(json.dumps(rows))
