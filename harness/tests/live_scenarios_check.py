"""Opt-in live scenarios against a running CADPilot (real model, real CAD kernel).

  CADPILOT_BASE=http://127.0.0.1:7811 CADPILOT_PASSWORD_FILE=harness/.docker/owner-password \\
      harness/.venv/bin/python harness/tests/live_scenarios_check.py plate vase knob

Drives a session over the agent WebSocket, answers questions with a canned reply, sends
follow-up messages once the assistant goes idle, and grades the outcome from the
exported STEP (measured in the CAD sandbox), the event trail and the final replies.
Evidence (event trail, summary, renders, STEP report) lands in runs/harness-checks/live-<stamp>/.
Nothing here touches other sessions or services.
"""
import argparse
import asyncio
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
BASE = os.environ.get('CADPILOT_BASE', 'http://127.0.0.1:7802')
PASSWORD_FILE = os.environ.get('CADPILOT_PASSWORD_FILE')
PLATE_VOLUME = (60 * 40 - (4 - math.pi) * 16 - 2 * math.pi * 1.7 ** 2 - (10 * 4 + math.pi * 4)) * 3
EVIDENCE = ROOT / 'runs/harness-checks/live-review-2026-09-14'


def near(value, target, tolerance):
    return value is not None and abs(value - target) <= tolerance


def size(step, index=None):
    if not step:
        return [None, None, None]
    return (step['per_solid'][index] if index is not None and index < len(step['per_solid']) else step['bounds'])['size']


def contours(step, key, closed_only=True):
    rows = (step or {}).get('sections', {}).get(key, [])
    return [c for c in rows if c['closed']] if closed_only else rows


def replies(summary):
    return ' '.join(summary.get('replies', []))


SCENARIOS = {
    'plate': {
        'task': ('Create a 60 x 40 x 3 mm mounting plate with 4 mm rounded corners, two 3.4 mm holes on the horizontal centreline '
                 '8 mm in from the left and right edges, and a 14 x 4 mm slot in the middle. Draw the outline as an SVG path using the '
                 'cad_paths generators, check it with path_preview before building, then build it from FreeCAD Python.'),
        'minutes': 20, 'sections': ['z=1.5'],
        'grade': lambda s: [('saved revision', bool(s['head'])),
                            ('path_preview used', 'path_preview' in s['tools']),
                            ('volume within 1%', bool(s['geometry']) and abs(s['geometry']['volume_mm3'] - PLATE_VOLUME) < PLATE_VOLUME * 0.01),
                            ('bounds 60x40x3', all(near(a, b, 0.05) for a, b in zip(size(s['step']), (60, 40, 3)))),
                            ('two 3.4 mm holes and the slot', sum(near(c['size'][0], 3.4, 0.05) and near(c['size'][1], 3.4, 0.05) for c in contours(s['step'], 'z=1.5')) == 2
                             and any(near(c['size'][0], 14, 0.05) and near(c['size'][1], 4, 0.05) for c in contours(s['step'], 'z=1.5')))],
    },
    'vase': {
        'task': ('Make a vase for 3D printing, 120 mm tall: the base outline is a rounded square 60 x 60 mm with 10 mm corner radius and it '
                 'lofts smoothly to a circle 80 mm in diameter at the top rim. Wall thickness 1.6 mm, closed bottom 3 mm thick, open top. '
                 'Build it from FreeCAD Python (cad_paths loft is available). Report the final outer dimensions and the wall thickness you achieved.'),
        'minutes': 25, 'sections': ['z=60', 'z=1.5', 'z=118'],
        'grade': lambda s: [('single solid', bool(s['step']) and s['step']['solids'] == 1),
                            ('outer 80x80x120', all(near(a, b, 0.6) for a, b in zip(size(s['step']), (80, 80, 120)))),
                            ('hollow (volume under 20% of envelope)', bool(s['step']) and s['step']['volume'] < 0.2 * 80 * 80 * 120),
                            ('solid bottom at z=1.5', len(contours(s['step'], 'z=1.5')) == 1),
                            ('wall 1.6 mm at mid height', len(contours(s['step'], 'z=60')) == 2 and
                             near((contours(s['step'], 'z=60')[0]['size'][0] - contours(s['step'], 'z=60')[1]['size'][0]) / 2, 1.6, 0.35)),
                            ('reply states dimensions', '120' in replies(s) and ('1.6' in replies(s) or '1,6' in replies(s)))],
    },
    'knob': {
        'task': ('Make a control knob for a potentiometer: 30 mm diameter, 14 mm tall, a gently domed top, six evenly spaced grip scallops '
                 '(4 mm radius) around the side, and a 6 mm D-shaft socket 10 mm deep with a 1 mm flat, open at the bottom. FreeCAD Python; '
                 'cad_paths has revolve and d_shape. Say which values you assumed.'),
        'minutes': 25, 'sections': ['z=5', 'z=12'],
        'grade': lambda s: [('single solid', bool(s['step']) and s['step']['solids'] == 1),
                            ('30 mm across, about 14 tall', near(size(s['step'])[0], 30, 0.8) and near(size(s['step'])[1], 30, 0.8) and near(size(s['step'])[2], 14, 1.0)),
                            ('D socket present at z=5 (6 x 5 mm)', any(near(min(c['size'][:2]), 5, 0.3) and near(max(c['size'][:2]), 6, 0.3) for c in contours(s['step'], 'z=5'))),
                            ('scallops remove material', bool(s['step']) and s['step']['volume'] < math.pi * 225 * 14 * 0.97),
                            ('assumptions stated', 'assum' in replies(s).lower())],
    },
    'enclosure': {
        'task': ('Make a two-part enclosure for a 50 x 30 mm circuit board that is 1.6 mm thick with components up to 8 mm tall. Walls 2 mm, '
                 'board clearance 0.5 mm per side, four M2.5 standoffs 4 mm tall under the board at holes 3.5 mm from each board corner, a snap-on '
                 'lid, and a 10 x 4 mm USB-C opening centred on one short wall at the level of the board top surface. Lay the base and lid side '
                 'by side for printing with a 5 mm gap. FreeCAD Python.'),
        'follow': ['Change the wall thickness to 2.4 mm and make the USB-C opening 12 mm wide. Keep everything else.'],
        'minutes': 35, 'sections': ['z=6'],
        'grade': lambda s: [('two solids', bool(s['step']) and s['step']['solids'] == 2),
                            ('parts separated by 5 mm', bool(s['step']) and near(s['step'].get('min_solid_distance'), 5, 0.6)),
                            ('base footprint after edit 55.8 x 35.8', any(near(sorted(p['size'][:2])[1], 55.8, 0.7) and near(sorted(p['size'][:2])[0], 35.8, 0.7) for p in (s['step'] or {}).get('per_solid', []))),
                            ('two saved revisions (edit rebuilt)', s['revisions'] >= 2),
                            ('four standoff bosses', sum(near(c['radius'], 1.375, 0.25) or near(c['radius'], 1.1, 0.2) for c in (s['step'] or {}).get('cylinders', [])) >= 4)],
    },
    'bracket': {
        'task': 'make a bracket',
        'minutes': 20,
        'grade': lambda s: [('asked before building', bool(s['questions']) and s['tools'].index('ask_question') < (s['tools'].index('cad_build') if 'cad_build' in s['tools'] else 10 ** 6)),
                            ('question offered choices', bool(s['questions']) and len(s['questions'][0].get('options', [])) >= 2),
                            ('built after the answer', bool(s['head'])),
                            ('reply states assumptions', 'assum' in replies(s).lower())],
    },
    'research_q': {
        'task': 'What is the mounting hole pattern of a Raspberry Pi 4 Model B? Give me the numbers with the source. Do not build anything.',
        'minutes': 25,
        'grade': lambda s: [('no build', 'cad_build' not in s['tools']),
                            ('research used', any(t in s['tools'] for t in ('research', 'research_dimensions'))),
                            ('58 and 49 mm in the reply', '58' in replies(s) and '49' in replies(s)),
                            ('source named', 'raspberrypi' in replies(s).lower() or 'http' in replies(s))],
    },
    'drawing_q': {
        # The attached sheet is the official Pi Zero *case* drawing: 79.00 x 37.70 mm outline,
        # 62.00 x 23.00 mm post pattern, no radius callout. A careful reader says so.
        'task': ('This is a mechanical drawing I have (attached). What outline size, corner radius and mounting hole pitch does it show? '
                 'Read them off the drawing. Do not build anything.'),
        'attach': [EVIDENCE / 'live-pizero-drawing.jpg'], 'minutes': 15,
        'grade': lambda s: [('no build', 'cad_build' not in s['tools']),
                            ('79 x 37.7 outline read', '79' in replies(s) and '37.7' in replies(s)),
                            ('62 x 23 pitch read', '62' in replies(s) and '23' in replies(s)),
                            ('radius reported as not dimensioned', 'radius' in replies(s).lower()),
                            ('no research needed', 'research_dimensions' not in s['tools'])],
    },
    'pizero': {
        'task': 'make me a case for the raspberry pi zero v1.1',
        'answer': 'Standard two-part snap-fit case, 2 mm walls, PLA. Keep every port and the SD slot accessible. Use your judgement for anything else and keep assumptions visible.',
        'minutes': 40,
        'grade': lambda s: [('research delegated', bool(s['research'])),
                            ('research reported values', any(r.get('documented_dimensions', 0) + r.get('visual_dimensions', 0) >= 6 for r in s['research'].values())),
                            ('saved revision with two solids', bool(s['step']) and s['step']['solids'] == 2),
                            ('plausible outer size', bool(s['step']) and 66 <= max(size(s['step'])[:2]) <= 95 and 31 <= min(size(s['step'])[:2]) <= 55)],
    },
}


async def step_report(step_path, sections):
    """Measure the exported STEP inside the CAD sandbox (no host FreeCAD needed)."""
    from server.cad_sandbox import execute
    directory = Path(tempfile.mkdtemp(prefix='.grade-', dir=ROOT / 'runs/harness-checks'))
    try:
        shutil.copyfile(step_path, directory / 'model.step')
        result = await execute(directory, ['/opt/cad/usr/bin/python', '/step_report.py', *sections], timeout=120, helpers=('step_report.py',))
        report = directory / 'report.json'
        if result['exitCode'] or not report.is_file():
            return None, result['output'][-800:]
        return json.loads(report.read_text()), ''
    finally:
        shutil.rmtree(directory, ignore_errors=True)


async def run(name, scenario, output):
    log = (output / f'{name}.log').open('w')
    summary = {'name': name, 'task': scenario['task'], 'started': time.time(), 'questions': [], 'tools': [], 'notes': [], 'errors': [],
               'research': {}, 'replies': [], 'turns': 0, 'follow_ups': [], 'step': None}

    def note(text):
        line = f'{time.strftime("%H:%M:%S")} {text}'
        print(f'[{name}] {line}', flush=True); log.write(line + '\n'); log.flush()

    async with httpx.AsyncClient(base_url=BASE, timeout=120) as client:
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
        attachments = []
        for path in scenario.get('attach', []):
            with open(path, 'rb') as handle:
                uploaded = await client.post(f'/api/sessions/{sid}/attachments', files={'file': (Path(path).name, handle, 'image/jpeg')})
            uploaded.raise_for_status()
            attachments.append(uploaded.json()['id']); note(f'attached {uploaded.json()}')
        for _ in range(120):
            row = next((s for s in (await client.get('/api/status')).json()['sessions'] if s['id'] == sid), None)
            if row and row.get('app_alive'):
                break
            await asyncio.sleep(1)
        answers = {}
        try:
            async with websockets.connect(BASE.replace('http', 'ws', 1) + f'/ws/agent/{sid}', max_size=64 * 1024 * 1024, additional_headers=headers) as ws:
                json.loads(await ws.recv())
                await ws.send(json.dumps({'t': 'start', 'task': scenario['task'], 'mode': 'auto', 'new_task': True, 'attachments': attachments}))
                deadline = time.time() + scenario['minutes'] * 60
                idle_since = pending = None
                follow = list(scenario.get('follow', []))
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
                            summary['questions'].append({'question': pending['question'], 'options': [o['label'] for o in options], 'answered': selected or answer})
                            pending = None
                        if idle_since and time.time() - idle_since > 20:
                            if follow:
                                text = follow.pop(0); idle_since = None
                                note(f'FOLLOW-UP {text!r}'); summary['follow_ups'].append(text)
                                await ws.send(json.dumps({'t': 'intent', 'text': text, 'attachments': []}))
                            else:
                                note('idle; finishing'); break
                        continue
                    kind = event.get('t')
                    if kind == 'answer_delta':
                        answers[event['answer_id']] = answers.get(event['answer_id'], '') + event.get('text', '')
                        continue
                    if kind in ('thinking_delta', 'tool_input_delta'):
                        continue
                    if kind == 'answer_done':
                        text = answers.pop(event['answer_id'], '').strip()
                        if text:
                            summary['replies'].append(text); note(f'REPLY {text[:220]!r}')
                    elif kind == 'tool_input_done':
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
                    elif kind == 'model_response':
                        summary['turns'] += 1
                    elif kind == 'done':
                        note(f"DONE {event.get('reason')}"); idle_since = time.time()
                    elif kind == 'phase' and event.get('phase') == 'awaiting':
                        idle_since = idle_since or time.time()
                    elif kind == 'user':
                        idle_since = None
                else:
                    note('time limit reached; stopping'); await ws.send(json.dumps({'t': 'stop'}))
            proj = (await client.get(f'/api/projects/{project}')).json() if project else {}
            summary['head'] = proj.get('head'); summary['revisions'] = len(proj.get('revisions', []))
            summary['geometry'] = {k: proj.get('geometry', {}).get(k) for k in ('bounds_mm', 'solid_count', 'volume_mm3')} if proj.get('geometry') else None
            if summary['head']:
                for file in ('view-iso.png', 'view-top.png', 'model.step', 'model.py'):
                    data = await client.get(f"/api/projects/{project}/{summary['head']}/{file}")
                    if data.status_code == 200:
                        (output / f'{name}-{file}').write_bytes(data.content)
                if (output / f'{name}-model.step').is_file():
                    summary['step'], error = await step_report(output / f'{name}-model.step', scenario.get('sections', []))
                    if error:
                        note('STEP report failed: ' + error[-300:])
        finally:
            await client.delete(f'/api/sessions/{sid}')
    summary['finished'] = time.time()
    summary['grades'] = scenario['grade'](summary)
    summary['passed'] = all(ok for _, ok in summary['grades'])
    (output / f'{name}.json').write_text(json.dumps(summary, indent=2))
    minutes = (summary['finished'] - summary['started']) / 60
    print(f"{'PASS' if summary['passed'] else 'FAIL'}: {name} in {minutes:.1f} min, {summary['turns']} model turns · "
          + ', '.join(f"{label}={'ok' if ok else 'NO'}" for label, ok in summary['grades']), flush=True)
    return summary['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('scenarios', nargs='*', default=['plate'], choices=list(SCENARIOS))
    parser.add_argument('--output', help='existing evidence directory to write into (default: a new runs/harness-checks/live-* directory)')
    args = parser.parse_args()
    output = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix='live-', dir=ROOT / 'runs/harness-checks'))
    output.mkdir(parents=True, exist_ok=True)
    print(output, flush=True)
    results = [asyncio.run(run(name, SCENARIOS[name], output)) for name in args.scenarios]
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
