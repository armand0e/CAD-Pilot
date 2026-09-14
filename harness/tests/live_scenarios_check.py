"""Opt-in live scenarios against a running CADPilot (real model, real CAD kernel).

  CADPILOT_BASE=http://127.0.0.1:7811 CADPILOT_PASSWORD_FILE=harness/.docker/owner-password \\
      harness/.venv/bin/python harness/tests/live_scenarios_check.py plate pizero

Drives a session over the agent WebSocket, answers questions with a canned reply,
and grades the outcome. Evidence (event trail, summary, renders) lands in
runs/harness-checks/live-<stamp>/. Nothing here touches other sessions or services.
"""
import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get('CADPILOT_BASE', 'http://127.0.0.1:7802')
PASSWORD_FILE = os.environ.get('CADPILOT_PASSWORD_FILE')
PLATE_VOLUME = (60 * 40 - (4 - math.pi) * 16 - 2 * math.pi * 1.7 ** 2 - (10 * 4 + math.pi * 4)) * 3

SCENARIOS = {
    'plate': {
        'task': ('Create a 60 x 40 x 3 mm mounting plate with 4 mm rounded corners, two 3.4 mm holes on the horizontal centreline '
                 '8 mm in from the left and right edges, and a 14 x 4 mm slot in the middle. Draw the outline as an SVG path using the '
                 'cad_paths generators, check it with path_preview before building, then build it from FreeCAD Python.'),
        'minutes': 20,
        'grade': lambda s: [('saved revision', bool(s['head'])),
                            ('path_preview used', 'path_preview' in s['tools']),
                            ('volume within 1%', bool(s['geometry']) and abs(s['geometry']['volume_mm3'] - PLATE_VOLUME) < PLATE_VOLUME * 0.01),
                            ('bounds 60x40x3', bool(s['geometry']) and all(abs(a - b) < 0.05 for a, b in zip(s['geometry']['bounds_mm'], (60, 40, 3))))],
    },
    'pizero': {
        'task': 'make me a case for the raspberry pi zero v1.1',
        'answer': 'Standard two-part snap-fit case, 2 mm walls, PLA. Keep every port and the SD slot accessible. Use your judgement for anything else and keep assumptions visible.',
        'minutes': 40,
        'grade': lambda s: [('research delegated', bool(s['research'])),
                            ('research reported values', any(r.get('documented_dimensions', 0) + r.get('visual_dimensions', 0) >= 6 for r in s['research'].values())),
                            ('saved revision with two solids', bool(s['geometry']) and s['geometry']['solid_count'] == 2),
                            ('plausible outer size', bool(s['geometry']) and 66 <= s['geometry']['bounds_mm'][0] <= 95 and 31 <= s['geometry']['bounds_mm'][1] <= 55)],
    },
}


async def run(name, scenario, output):
    log = (output / f'{name}.log').open('w')
    summary = {'name': name, 'task': scenario['task'], 'started': time.time(), 'questions': [], 'tools': [], 'notes': [], 'errors': [], 'research': {}}

    def note(text):
        line = f'{time.strftime("%H:%M:%S")} {text}'
        print(line, flush=True); log.write(line + '\n'); log.flush()

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        headers = {}
        if PASSWORD_FILE:
            password = Path(PASSWORD_FILE).read_text().strip()
            login = await client.post('/api/auth/login', json={'username': os.environ.get('CADPILOT_OWNER', 'admin'), 'password': password})
            if login.status_code != 200:
                raise SystemExit(f'login failed: {login.status_code} {login.text[:200]}')
            headers = {'Cookie': '; '.join(f'{k}={v}' for k, v in client.cookies.items()), 'Origin': BASE}
        apps = (await client.get('/api/apps')).json()['apps']
        app_id = next(a['id'] for a in apps if 'freecad' in a['id'])
        session = (await client.post('/api/sessions', json={'app_id': app_id, 'engine': 'hybrid'})).json()
        sid, project = session['id'], session.get('project_id') or session.get('project', {}).get('id')
        summary['session'], summary['project'] = sid, project
        note(f'session {sid} project {project}')
        for _ in range(120):
            row = next((s for s in (await client.get('/api/status')).json()['sessions'] if s['id'] == sid), None)
            if row and row.get('app_alive'):
                break
            await asyncio.sleep(1)
        try:
            async with websockets.connect(BASE.replace('http', 'ws', 1) + f'/ws/agent/{sid}', max_size=64 * 1024 * 1024, additional_headers=headers) as ws:
                json.loads(await ws.recv())
                await ws.send(json.dumps({'t': 'start', 'task': scenario['task'], 'mode': 'auto', 'new_task': True, 'attachments': []}))
                deadline = time.time() + scenario['minutes'] * 60
                idle_since = pending = None
                while time.time() < deadline:
                    try:
                        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        if pending and time.time() - pending['at'] > 15:
                            options = pending.get('options') or []
                            selected = [options[0]['label']] if options else []
                            answer = scenario.get('answer', 'Use your best engineering judgement and keep assumptions visible.')
                            note(f"ANSWERING {pending['question'][:100]!r} -> {selected or answer}")
                            await ws.send(json.dumps({'t': 'answer', 'question_id': pending['question_id'], 'selected': selected, 'text': '' if selected else answer}))
                            summary['questions'].append({'question': pending['question'], 'answered': selected or answer})
                            pending = None
                        if idle_since and time.time() - idle_since > 20:
                            note('idle; finishing'); break
                        continue
                    kind = event.get('t')
                    if kind in ('thinking_delta', 'answer_delta', 'tool_input_delta'):
                        continue
                    if kind == 'tool_input_done':
                        summary['tools'].append(event.get('tool')); note(f"TOOL {event.get('tool')} {json.dumps(event.get('arguments'))[:200]}")
                    elif kind == 'tool_settled' and event.get('status') != 'completed':
                        note(f"  settled {event.get('status')} {event.get('message', '')[:200]}")
                    elif kind == 'research_agent':
                        if event.get('step'):
                            note(f"  research {event.get('status')} {event['step']['activity']} · {event['step'].get('detail', '')[:120]}")
                        summary['research'][event.get('agent_id')] = {k: event.get(k) for k in ('status', 'searches', 'pages_read', 'calls', 'documented_dimensions', 'visual_dimensions')}
                    elif kind == 'question':
                        pending = {**event, 'at': time.time()}; note(f"QUESTION {event.get('question')!r}")
                    elif kind in ('note', 'error'):
                        (summary['errors'] if kind == 'error' else summary['notes']).append((event.get('message') or '')[:200]); note(f"{kind.upper()} {(event.get('message') or '')[:200]}")
                    elif kind == 'artifact':
                        geometry = event.get('project', {}).get('geometry', {})
                        note(f"ARTIFACT head={event.get('project', {}).get('head')} bounds={geometry.get('bounds_mm')} solids={geometry.get('solid_count')}")
                    elif kind == 'done':
                        note(f"DONE {event.get('reason')}"); idle_since = time.time()
                    elif kind == 'phase' and event.get('phase') == 'awaiting':
                        idle_since = idle_since or time.time()
                    elif kind == 'user':
                        idle_since = None
                else:
                    note('time limit reached; stopping'); await ws.send(json.dumps({'t': 'stop'}))
            proj = (await client.get(f'/api/projects/{project}')).json() if project else {}
            summary['head'] = proj.get('head')
            summary['geometry'] = {k: proj.get('geometry', {}).get(k) for k in ('bounds_mm', 'solid_count', 'volume_mm3')} if proj.get('geometry') else None
            if summary['head']:
                for view in ('iso', 'top'):
                    image = await client.get(f"/api/projects/{project}/{summary['head']}/view-{view}.png")
                    if image.status_code == 200:
                        (output / f'{name}-{view}.png').write_bytes(image.content)
        finally:
            await client.delete(f'/api/sessions/{sid}')
    summary['finished'] = time.time()
    summary['grades'] = scenario['grade'](summary)
    summary['passed'] = all(ok for _, ok in summary['grades'])
    (output / f'{name}.json').write_text(json.dumps(summary, indent=2))
    minutes = (summary['finished'] - summary['started']) / 60
    print(f"{'PASS' if summary['passed'] else 'FAIL'}: {name} in {minutes:.1f} min · " + ', '.join(f"{label}={'ok' if ok else 'NO'}" for label, ok in summary['grades']), flush=True)
    return summary['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('scenarios', nargs='*', default=['plate'], choices=list(SCENARIOS))
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix='live-', dir=ROOT / 'runs/harness-checks'))
    print(output, flush=True)
    passed = all(asyncio.run(run(name, SCENARIOS[name], output)) for name in args.scenarios)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
