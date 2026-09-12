"""Stock-model benchmark: run each task through the real harness (own session per task),
answer questions with scripted replies, then grade the final revision deterministically.
Usage: harness/.venv/bin/python tests/benchmark_run.py --base http://127.0.0.1:7802 [--only plate_holes,spacer] [--seconds 1500]
Evidence lands under runs/harness-checks/benchmark-*/.
"""
import argparse, asyncio, json, math, sys, tempfile, time
from pathlib import Path
import httpx, websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
from server.operations import primitive_bounds  # noqa: E402
from server.design import expression  # noqa: E402


def evaluate(text):
    """Grader arithmetic: numbers, pi, + - * / ^ and parentheses only."""
    return eval(text.replace('^', '**'), {'__builtins__': {}}, {'pi': math.pi})


def grade(spec, design, geometry, revisions):
    checks = []
    def check(name, ok, detail=''):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    if 'parts' in spec:
        check('parts', geometry['solid_count'] == spec['parts'], f"{geometry['solid_count']} vs {spec['parts']}")
    if 'bounds_mm' in spec:
        check('bounds', all(abs(a - b) <= 0.05 for a, b in zip(geometry['bounds_mm'], spec['bounds_mm'])), f"{[round(v, 3) for v in geometry['bounds_mm']]} vs {spec['bounds_mm']}")
    if 'bounds_range_mm' in spec:
        for axis, (lo, hi) in spec['bounds_range_mm'].items():
            value = geometry['bounds_mm']['xyz'.index(axis)]
            check(f'bounds {axis} in range', lo <= value <= hi, f'{value:.2f} in [{lo}, {hi}]')
    if 'volume_mm3' in spec:
        expect = evaluate(spec['volume_mm3']['expect'])
        check('volume', abs(geometry['volume_mm3'] - expect) <= expect * spec['volume_mm3']['tolerance_pct'] / 100, f"{geometry['volume_mm3']:.1f} vs {expect:.1f}")
    if 'min_revisions' in spec:
        check('revisions', len(revisions) >= spec['min_revisions'], f'{len(revisions)} revisions')
    if 'kinds_any' in spec:
        kinds = {f['kind'] for f in design['features']}
        check('uses profile kind', bool(kinds & set(spec['kinds_any'])), f'kinds {sorted(kinds)}')
    if spec.get('hollow'):
        # Measure hollowness; do not prescribe how it was built.
        bbox = math.prod(geometry['bounds_mm'])
        check('hollow', geometry['volume_mm3'] < 0.35 * bbox, f"volume {geometry['volume_mm3']:.0f} of bbox {bbox:.0f}")
    if 'part_bounds_mm' in spec:
        parts = geometry.get('parts', [])
        sizes = sorted([[round(p['max_mm'][i] - p['min_mm'][i], 3) for i in range(3)] for p in parts])
        wanted = sorted(spec['part_bounds_mm'])
        check('part bounds', len(parts) == len(wanted) and all(all(abs(a - b) <= 0.05 for a, b in zip(s, w)) for s, w in zip(sizes, wanted)), f'{sizes} vs {wanted}')
    if 'part_volume_mm3' in spec:
        parts = sorted(geometry.get('parts', []), key=lambda p: p['volume_mm3'])
        wanted = sorted(evaluate(v['expect']) for v in spec['part_volume_mm3'])
        tolerance = max(v['tolerance_pct'] for v in spec['part_volume_mm3'])
        check('part volumes', len(parts) == len(wanted) and all(abs(p['volume_mm3'] - w) <= w * tolerance / 100 for p, w in zip(parts, wanted)), f'{[round(p["volume_mm3"]) for p in parts]} vs {[round(w) for w in wanted]}')
    if 'min_part_gap_mm' in spec:
        parts = geometry.get('parts', [])
        gap = None
        if len(parts) == 2:
            a, b = parts
            gap = max(max(a['min_mm'][i] - b['max_mm'][i], b['min_mm'][i] - a['max_mm'][i]) for i in range(3))
        check('part gap', gap is not None and gap >= spec['min_part_gap_mm'], f'gap {gap}')
    if 'through_holes' in spec:
        wanted = spec['through_holes']
        params = {p['name']: p['value'] for p in design['parameters']}
        axis = 'xyz'.index(wanted['axis'])
        cutters = []
        for feature in design['features']:
            if feature['kind'] != 'cylinder' or feature['id'].endswith('_cavity'):
                continue
            dims = [expression(d, params)[0] for d in feature['dimensions']]
            position = [expression(p, params)[0] for p in feature['position']]
            bounds = primitive_bounds('cylinder', dims, position, feature['rotation'])
            if not bounds:
                continue
            length = bounds[axis][1] - bounds[axis][0]
            radial = [i for i in range(3) if i != axis]
            if abs(bounds[radial[0]][1] - bounds[radial[0]][0] - wanted['diameter_mm']) <= wanted['tolerance_mm'] and length >= geometry['bounds_mm'][axis] - 0.5:
                cutters.append([round((bounds[i][0] + bounds[i][1]) / 2, 3) for i in radial])
        check('through hole count', len(cutters) == wanted['count'], f'{len(cutters)} cutters of {wanted["diameter_mm"]} mm along {wanted["axis"]}: {cutters}')
        if 'centers_xy' in wanted:
            ok = all(any(abs(c[0] - w[0]) <= wanted['tolerance_mm'] and abs(c[1] - w[1]) <= wanted['tolerance_mm'] for c in cutters) for w in wanted['centers_xy'])
            check('hole centers', ok, f'{cutters} vs {wanted["centers_xy"]}')
    return {'passed': all(c['ok'] for c in checks), 'checks': checks}


async def run_task(base, task, seconds, log):
    async with httpx.AsyncClient(timeout=60) as client:
        apps = (await client.get(base + '/api/apps')).json()['apps']
        app = next(a for a in apps if 'freecad' in a['id'])
        session = (await client.post(base + '/api/sessions', json={'app_id': app['id'], 'engine': 'hybrid'})).json()
        sid, project_id = session['id'], session.get('project_id')
        events, questions, outcome, started = [], 0, 'timeout', time.monotonic()
        try:
            async with websockets.connect(base.replace('http', 'ws') + f'/ws/agent/{sid}', max_size=None) as ws:
                await ws.send(json.dumps({'t': 'start', 'task': task['prompt'], 'mode': 'auto', 'new_task': True}))
                while time.monotonic() - started < seconds:
                    try:
                        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    except asyncio.TimeoutError:
                        continue
                    events.append(event)
                    kind = event.get('t')
                    if kind in ('intent', 'tool_error', 'question', 'done', 'error', 'native_review', 'pause'):
                        log.write(f"{task['id']} {time.monotonic()-started:6.0f}s {json.dumps({k: str(v)[:160] for k, v in event.items() if k in ('t','text','message','reason','status','question','plan')})}\n"); log.flush()
                    if kind == 'question':
                        questions += 1
                        options = event.get('options') or []
                        reply = {'t': 'answer', 'question_id': event['question_id'], 'selected': [options[0]['label']] if options else [], 'text': '' if options else task['answers']['default']}
                        await asyncio.sleep(0.5); await ws.send(json.dumps(reply))
                    if kind == 'pause' and event.get('paused') and event.get('reason'):
                        outcome = 'paused'; break
                    if kind == 'done':
                        outcome = 'done'; break
                    if kind == 'error':
                        outcome = 'error'; break
        finally:
            info = (await client.get(base + f'/api/projects/{project_id}')).json() if project_id else None
            await client.delete(base + f'/api/sessions/{sid}')
        return {'task': task['id'], 'outcome': outcome, 'seconds': round(time.monotonic() - started), 'questions': questions,
                'checkpoints': sum(1 for e in events if e.get('t') == 'step_done'), 'rejected': sum(1 for e in events if e.get('t') == 'tool_error'),
                'project': project_id, 'info': info, 'events': events}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:7802')
    parser.add_argument('--only', default='')
    parser.add_argument('--seconds', type=int, default=1500)
    args = parser.parse_args()
    tasks = json.loads((Path(__file__).resolve().parent / 'benchmark/tasks.json').read_text())['tasks']
    if args.only:
        tasks = [t for t in tasks if t['id'] in args.only.split(',')]
    out = Path(tempfile.mkdtemp(prefix='benchmark-', dir=ROOT / 'runs/harness-checks'))
    results = []
    with (out / 'events.log').open('w') as log:
        for task in tasks:
            result = await run_task(args.base, task, args.seconds, log)
            info = result.pop('info', None) or {}
            if info.get('head'):
                async with httpx.AsyncClient(timeout=60) as client:
                    design = (await client.get(f"{args.base}/api/projects/{result['project']}/{info['head']}/design.json")).json()
                    geometry = (await client.get(f"{args.base}/api/projects/{result['project']}/{info['head']}/geometry.json")).json()
                result['grade'] = grade(task['grade'], design, geometry, info.get('revisions', []))
            else:
                result['grade'] = {'passed': False, 'checks': [{'check': 'saved revision', 'ok': False, 'detail': 'no revision saved'}]}
            (out / f"{task['id']}.events.json").write_text(json.dumps(result.pop('events'), indent=1))
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != 'grade'}), '->', 'PASS' if result['grade']['passed'] else 'FAIL',
                  [c['check'] + ('' if c['ok'] else f" ({c['detail']})") for c in result['grade']['checks'] if not c['ok']], flush=True)
    summary = {'passed': sum(1 for r in results if r['grade']['passed']), 'total': len(results), 'results': results}
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(f"{summary['passed']}/{summary['total']} passed; evidence {out}")


if __name__ == '__main__':
    asyncio.run(main())
