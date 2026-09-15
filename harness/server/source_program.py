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

# Optional model-chosen saved views: views = {"name": [dx, dy, dz], ...}, each a camera
# direction (object -> camera, as cad_render). Validated here; the kernel renders them.
import re as _re
views = namespace.get('views')
if isinstance(views, dict) and views:
    chosen = {}
    for name, direction in list(views.items())[:8]:
        key = _re.sub(r'[^a-z0-9_-]+', '-', str(name).lower()).strip('-')[:24]
        try:
            vec = [float(c) for c in direction]
        except (TypeError, ValueError):
            continue
        if key and len(vec) == 3 and all(c == c and abs(c) != float('inf') for c in vec) and any(vec):
            chosen[key] = vec
    if chosen:
        Path('build-views.json').write_text(json.dumps(chosen))
