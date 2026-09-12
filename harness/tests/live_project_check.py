"""Opt-in stock-model native-tools benchmark; no templates supplied to the model.

Natural request -> real browser -> stock planner -> recipe -> sandboxed compiler ->
saved artifacts -> separate reference-shape grader. Does not train/load any weights.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'training'))
from cad_policy.cad_environment import CADEnvironment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', choices=['freecad', 'openscad'], default='freecad')
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix=f'projects-{args.app}-', dir=ROOT / 'runs/harness-checks'))
    print(str(output), flush=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1440, 'height':1000}, bypass_csp=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sid = None
        base = 'http://127.0.0.1:7802'
        try:
            page.goto(base, wait_until='networkidle')
            page.locator('.app-tile', has_text='FreeCAD' if args.app == 'freecad' else 'OpenSCAD').click()
            page.wait_for_function("document.querySelector('#connection-status').textContent === 'Live'")
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            for index, (prompt, length, radius) in enumerate([
                ('Create a 60 × 40 × 8 mm rectangular plate with four 5 mm diameter through mounting holes, each centered 8 mm from the adjacent edges. Use (0,0,0) as the minimum corner.', 60, 2.5),
                ('Make it 80 mm long and make the mounting holes 6 mm diameter. Keep the width, thickness and edge offsets unchanged.', 80, 3),
            ]):
                page.fill('#composer-input', prompt)
                page.locator('#btn-send').click()
                deadline = time.monotonic() + 180
                snapshot = {}
                while time.monotonic() < deadline:
                    snapshot = page.request.get(f'{base}/api/sessions/{sid}/activity').json()
                    if any(e['t'] == 'user' and e['text'] == prompt for e in snapshot['events']) and (not snapshot['active'] or snapshot['paused']):
                        break
                    page.wait_for_timeout(500)
                (output / f'activity-{index}.json').write_text(json.dumps(snapshot, indent=2))
                if snapshot.get('active') or snapshot.get('paused'):
                    raise AssertionError('Agent did not finish the native task: ' + json.dumps(snapshot['events'][-8:]))
                artifacts = [e for e in snapshot['events'] if e['t'] == 'artifact']
                assert len(artifacts) == index + 1, snapshot['events'][-8:]
                project = artifacts[-1]['project']
                assert not any(e['t']=='note' and 'Viewport warning' in e.get('message','') for e in snapshot['events']), 'Native artifacts built but viewport presentation failed'
                directory = output / f'grade-{index}'
                work = directory / 'work'; work.mkdir(parents=True)
                artifact = 'model.FCStd' if args.app == 'freecad' else 'model.scad'
                response = page.request.get(f"{base}/api/projects/{project['id']}/{project['head']}/{artifact}")
                assert response.ok, response.text()
                (work / artifact).write_bytes(response.body())
                env = object.__new__(CADEnvironment)
                env.task = {'app':args.app, 'length':length, 'width':40, 'height':8,
                            'holes':True, 'radius':radius, 'offset':8, 'artifact':artifact}
                env.directory, env.work = directory, work
                result = env.reward()
                results.append(result)
                print(json.dumps({'revision':project['head'], 'result':result}), flush=True)
                assert result.get('success'), result
                page.screenshot(path=str(output / f'workspace-{index}.png'))
            # Restore is append-only, survives reload and reopening, and serves intact artifacts.
            response = page.request.post(f'{base}/api/sessions/{sid}/project', data={
                'operation':'restore', 'revision':'r0001', 'expected_head':'r0002'})
            assert response.ok, response.text()
            assert not response.json().get('warning'), response.json()
            assert response.json()['head'] == 'r0003'
            project_id = project['id']
            page.request.delete(f'{base}/api/sessions/{sid}'); sid = None
            reopened = page.request.post(f'{base}/api/sessions', data={'app_id':project['app_id'], 'project_id':project_id})
            assert reopened.ok, reopened.text()
            sid = reopened.json()['id']
            assert not reopened.json().get('warning'), reopened.json()
            page.evaluate('(sid)=>localStorage.setItem("cadpilot-session",sid)', sid)
            page.reload(wait_until='networkidle')
            page.wait_for_function("document.querySelector('#model-name').textContent.includes('r0003')")
            page.locator('#screen:not(.hidden)').wait_for()
            page.wait_for_timeout(1500)
            page.locator('#model-panel').evaluate('(e)=>e.open=true')
            page.screenshot(path=str(output / 'reopened.png'))
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output / 'mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
            assert not errors, errors
        finally:
            sid = sid or page.evaluate("localStorage.getItem('cadpilot-session')")
            if sid:
                page.request.delete(f'{base}/api/sessions/{sid}')
            browser.close()
    (output / 'result.json').write_text(json.dumps({'results':results, 'reopen_restore':True, 'browser_errors':errors}, indent=2))
    print(json.dumps({'output':str(output), 'success':True}), flush=True)


if __name__ == '__main__':
    main()
