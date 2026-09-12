"""Stock-model browser regression for the fully specified enclosure seed.

Only natural-language dimensions are sent. This is a known regression case, NOT
a held-out model capability score. Never opens the user's main session.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import tempfile
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'training/curated'))
from seed_cases import episodes
from replay import verify, Project


def main():
    output = Path(tempfile.mkdtemp(prefix='enclosure-live-', dir=ROOT / 'runs/harness-checks'))
    print(output, flush=True)
    prompt = next(e for e in episodes() if e['id'] == 'vented-enclosure')['steps'][0]['user']
    base = 'http://127.0.0.1:7802'
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        sid = None
        try:
            page.goto(base, wait_until='networkidle')
            page.locator('.app-tile', has_text='FreeCAD').click()
            expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            # Reproduce the reported fresh-session viewport activity before sending.
            page.locator('#screen').click(position={'x': 500, 'y': 400})
            page.fill('#composer-input', prompt)
            page.locator('#btn-send').click()
            deadline = time.monotonic() + 600
            snapshot = {}
            while time.monotonic() < deadline:
                snapshot = page.request.get(f'{base}/api/sessions/{sid}/activity').json()
                if snapshot.get('events') and (not snapshot['active'] or snapshot['paused']):
                    break
                page.wait_for_timeout(500)
            (output / 'activity.json').write_text(json.dumps(snapshot, indent=2))
            page.screenshot(path=str(output / 'workspace.png'))
            assert not snapshot.get('active') and not snapshot.get('paused'), snapshot.get('events', [])[-8:]
            artifacts = [e for e in snapshot['events'] if e['t'] == 'artifact']
            assert len(artifacts) >= 1, snapshot['events'][-8:]
            result = artifacts[-1]['project']
            assert result['geometry']['solid_count'] == 2
            assert not any('Viewport warning' in e.get('message', '') for e in snapshot['events'])
            project = Project(ROOT / 'runs/harness-checks/native-projects', result['id'])
            # Stock model parameter names are unconstrained. The initial shape is
            # exactly graded; the edit must change geometry and survive reopen.
            # Do not invent an unstated vent-relocation requirement for that edit.
            # Playwright's sync wrapper owns a running asyncio loop on this thread.
            # Run the independent sandboxed grader in its own thread/event loop.
            with ThreadPoolExecutor(max_workers=1) as pool:
                checked = pool.submit(lambda: asyncio.run(verify(project, result, 'enclosure', output / 'verification', strict_edit_oracle=False))).result(timeout=180)
            page.reload(wait_until='networkidle')
            expect(page.locator('#model-status')).to_contain_text('2 valid parts')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            (output / 'result.json').write_text(json.dumps({'success': True, 'verification': checked, 'browser_errors': errors}, indent=2))
            print(json.dumps({'success': True, 'output': str(output)}), flush=True)
        finally:
            sid = sid or page.evaluate("localStorage.getItem('cadpilot-session')")
            if sid:
                page.request.delete(f'{base}/api/sessions/{sid}')
            browser.close()


if __name__ == '__main__':
    main()
