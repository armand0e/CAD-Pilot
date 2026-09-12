"""Opt-in browser check of the authenticated Docker deployment; optional real model turns."""
import argparse
import io
import json
from pathlib import Path
import tempfile
import time

from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:7801')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--password-file', type=Path, default=ROOT / 'harness/.docker/owner-password')
    parser.add_argument('--live-model', action='store_true')
    parser.add_argument('--model-timeout', type=float, default=600, help='Test wait only; 0 waits indefinitely')
    args = parser.parse_args()
    directory = ROOT / 'runs/harness-checks'
    directory.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='docker-', dir=directory))
    print(str(output), flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sid = None
        try:
            page.goto(args.base, wait_until='networkidle')
            assert page.url.endswith('/login')
            page.screenshot(path=str(output / 'login.png'))
            page.fill('#username', args.username)
            page.fill('#password', args.password_file.read_text().strip())
            page.locator('button[type=submit]').click()
            page.wait_for_url(args.base + '/')
            page.locator('#btn-settings').click()
            page.locator('#btn-logout').wait_for()
            page.locator('#settings-close').click()
            status = page.request.get(args.base + '/api/status').json()
            assert status['research']['backend'] == 'searxng'
            assert status['planner']['ok'] and status['policy']['ok'], status
            page.locator('.app-tile', has_text='FreeCAD').click()
            expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            assert sid
            # A >32 KiB upload must reach the attachment handler and produce an image.
            image = io.BytesIO()
            Image.effect_noise((400, 400), 80).convert('RGB').save(image, 'PNG')
            assert len(image.getvalue()) > 32768
            upload = page.request.post(args.base + f'/api/sessions/{sid}/attachments', multipart={
                'file': {'name': 'fixture.png', 'mimeType': 'image/png', 'buffer': image.getvalue()}})
            assert upload.ok, upload.text()
            if args.live_model:
                for index, (prompt, expected_volume) in enumerate([
                    ('Create a solid box 20 x 10 x 5 mm. Make height a named parameter called height. No web research needed.', 1000),
                    ('Change the height to 6 mm. Keep length and width unchanged.', 1200),
                ]):
                    start = time.monotonic()
                    page.fill('#composer-input', prompt)
                    page.locator('#btn-send').click()
                    while True:
                        snapshot = page.request.get(args.base + f'/api/sessions/{sid}/activity').json()
                        user = next((e for e in reversed(snapshot['transcript']) if e['t'] == 'user' and e.get('text') == prompt), None)
                        if user and snapshot.get('phase') == 'awaiting' and any(e['t'] == 'done' and e.get('turn_id') == user.get('turn_id') for e in snapshot['events']):
                            break
                        if args.model_timeout and time.monotonic() - start > args.model_timeout:
                            raise AssertionError('Test model wait exhausted')
                        page.wait_for_timeout(500)
                    (output / f'activity-{index}.json').write_text(json.dumps(snapshot, indent=2))
                    project_id = page.request.get(args.base + '/api/status').json()['sessions'][0]['project_id']
                    project = page.request.get(args.base + f'/api/projects/{project_id}').json()
                    assert abs(project['geometry']['volume_mm3'] - expected_volume) < .001, project
                    assert not any(e['t'] == 'note' and 'Viewport warning' in e.get('message', '') for e in snapshot['events'])
                    print(f'PASS: live model turn {index + 1}, volume {expected_volume}, {time.monotonic() - start:.1f}s', flush=True)
                    download = page.request.get(args.base + f'/api/projects/{project_id}/{project["head"]}/model.step')
                    assert download.ok and len(download.body()) > 100
                    (output / f'model-{index}.step').write_bytes(download.body())
            page.screenshot(path=str(output / 'workspace.png'))
            page.reload()
            expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.request.delete(args.base + f'/api/sessions/{sid}')
            sid = None
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('#btn-settings').click()
            page.locator('#btn-logout').click()
            page.wait_for_url(args.base + '/login')
            assert page.request.get(args.base + '/api/settings').status == 401
            assert not errors, errors
            (output / 'result.json').write_text(json.dumps({'success': True, 'live_model': args.live_model, 'browser_errors': errors}, indent=2))
            print('PASS: login, CAD viewport, attachment upload, reload, mobile layout, logout', flush=True)
        finally:
            sid = sid or page.evaluate("localStorage.getItem('cadpilot-session')")
            if sid:
                context.request.delete(args.base + f'/api/sessions/{sid}')
            browser.close()


if __name__ == '__main__':
    main()
