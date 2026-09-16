"""User-triggered advisory review of a saved revision.

The agent has an on-demand `review` tool, but the model rarely calls it - so the taste check
never applies. This runs the SAME reviewer (its prompt and schema) against a saved revision on
demand, from the user's Review button or, when enabled, automatically after a build. It rebuilds
the reviewer's context from the committed revision (geometry, spec, rendered views) instead of a
live agent turn, so it works for any revision, native or source (model.bpy / model.scad)."""
import base64
import json

import httpx

from .operations import REQUIREMENT_REVIEW_SCHEMA, REQUIREMENT_REVIEW_SYSTEM

# Keep the payload small: the reviewer mainly needs the views plus a geometry summary, not the
# full mesh dump. These are the fields it reasons over.
_GEOMETRY_KEYS = ('solid_count', 'bounds', 'valid_solid', 'printable', 'representation',
                  'notice', 'views', 'result_object')


def _load_json(project, head, name):
    try:
        return json.loads(project.file(head, name).read_text())
    except (OSError, ValueError):
        return {}


def _view_parts(project, head, limit=4):
    """Rendered views of the revision as image parts, so the reviewer judges form, not only bounds."""
    entry = next((r for r in project.read().get('revisions', []) if r['id'] == head), {})
    names = [n[5:-4] for n in sorted(entry.get('sha256', {})) if n.startswith('view-') and n.endswith('.png')]
    # Prefer the lit beauty views (render + the model's own angles) over the grey ortho ones.
    ordered = [v for v in ('render', 'face', 'three-quarter', 'threeq', 'front', 'iso', 'right', 'top') if v in names]
    ordered += [v for v in names if v not in ordered]
    parts = []
    for view in ordered:
        try:
            data = project.file(head, f'view-{view}.png').read_bytes()
        except (OSError, ValueError):
            continue
        parts += [{'type': 'text', 'text': f'Rendered {view} view of the saved model:'},
                  {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data).decode()}}]
        if len(parts) // 2 >= limit:
            break
    return parts


def _validate(review):
    if (not isinstance(review, dict) or set(review) != {'status', 'issues', 'summary'} or
            review['status'] not in ('satisfactory', 'revise', 'needs_input') or
            not isinstance(review['issues'], list) or len(review['issues']) > 12 or
            any(not isinstance(v, str) or len(v) > 240 for v in review['issues']) or
            not isinstance(review['summary'], str) or not 1 <= len(review['summary']) <= 350 or
            (review['status'] == 'satisfactory' and review['issues'])):
        raise ValueError('Review response was malformed or inconsistent')
    return review


async def review_revision(project, head, config):
    """Run the advisory reviewer on one saved revision. Returns {status, issues, summary}."""
    planner = config.get('planner') or {}
    if not planner.get('base_url') or not planner.get('model'):
        raise ValueError('No model endpoint is configured for review')
    geometry = _load_json(project, head, 'geometry.json')
    spec = _load_json(project, head, 'design-spec.json')
    design = _load_json(project, head, 'design.json')
    summary = json.dumps({
        'task': spec.get('objective') or project.read().get('name', ''),
        'design_spec': spec,
        'geometry': {k: geometry[k] for k in _GEOMETRY_KEYS if k in geometry},
        'engine': design.get('language', 'unknown'),
        'note': ('A user requested this review of a saved revision. Judge it against the task and, '
                 'for an artistic subject, the rendered views - craft quality counts, not only that '
                 'the parts are present.')})
    views = _view_parts(project, head)
    content = ([{'type': 'text', 'text': summary}] + views) if views else summary
    headers = {'Authorization': 'Bearer ' + planner['api_key']} if planner.get('api_key') else {}
    payload = {'model': planner['model'], 'temperature': 0, 'max_tokens': 6144,
               'messages': [{'role': 'system', 'content': REQUIREMENT_REVIEW_SYSTEM},
                            {'role': 'user', 'content': content}],
               'response_format': {'type': 'json_schema', 'json_schema': {
                   'name': 'cad_requirement_review', 'strict': True, 'schema': REQUIREMENT_REVIEW_SCHEMA}}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), headers=headers) as client:
        response = await client.post(planner['base_url'].rstrip('/') + '/chat/completions', json=payload)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Empty review response')
    review = _validate(json.loads(content))
    # Small reviewer models sometimes repeat one issue to fill the list; collapse duplicates.
    review['issues'] = list(dict.fromkeys(review['issues']))
    return review
