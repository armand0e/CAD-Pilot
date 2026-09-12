"""Opt-in local portal + Docker runtime integration, including real model turns.
Start a test portal on 127.0.0.1:7804 before running. Uses only new test accounts/volumes.
"""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import zipfile

import yaml
from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
BASE = 'http://127.0.0.1:7804'


def main():
    os.umask(0o077)
    output = Path(tempfile.mkdtemp(prefix='portal-', dir=ROOT / 'runs/harness-checks'))
    print(output, flush=True)
    password = secrets.token_urlsafe(24)
    (output / 'test-password').write_text(password)
    commands = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sid = None
        try:
            page.goto(BASE)
            page.locator('#auth-toggle').click()
            page.fill('#username', 'check-' + secrets.token_hex(4))
            page.fill('#password', password)
            page.locator('button[type=submit]').click()
            page.wait_for_url(BASE + '/onboarding')
            page.screenshot(path=str(output / 'onboarding.png'))
            assert page.request.get(BASE + '/api/projects').status == 503
            with page.expect_download() as downloading:
                page.locator('#download').click()
            downloading.value.save_as(output / 'setup.zip')
            with zipfile.ZipFile(output / 'setup.zip') as archive:
                assert 'source/harness/server/agent.py' in archive.namelist()
                assert 'source/harness/web/chat/panel.js' in archive.namelist()
                assert all('/.docker/' not in name and 'owner-password' not in name for name in archive.namelist())
                archive.extractall(output / 'setup')
            compose_path = output / 'setup/compose.yaml'
            compose = yaml.safe_load(compose_path.read_text())
            cad = compose['services']['cad']
            cad.pop('build', None)
            cad['image'] = 'cadpilot:local'
            # The local portal also listens on Docker's host gateway for this test.
            (output / 'setup/searxng').chmod(0o755)
            (output / 'setup/searxng/settings.yml').chmod(0o644)
            compose_path.write_text(yaml.safe_dump(compose, sort_keys=False))
            commands = ['docker', 'compose', '-f', str(compose_path)]
            with (output / 'docker-start.txt').open('w') as log:
                subprocess.run(commands + ['up', '-d', '--no-build', '--wait'], check=True, stdout=log, stderr=log)
            expect(page.locator('#connection')).to_have_text('Connected', timeout=60000)
            page.locator('#enter').click()
            page.wait_for_url(BASE + '/', timeout=30000)
            page.locator('.app-tile', has_text='FreeCAD').click()
            expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            assert sid
            picture = io.BytesIO(); Image.effect_noise((800, 800), 80).convert('RGB').save(picture, 'PNG')
            uploaded = page.request.post(BASE + f'/api/sessions/{sid}/attachments', multipart={
                'file': {'name': 'reference.png', 'mimeType': 'image/png', 'buffer': picture.getvalue()}})
            assert uploaded.ok, uploaded.text()
            print('PASS: signup → downloaded source bundle → Docker pairing → model check → live CAD, upload', flush=True)
            def wait_turn(prompt):
                started = time.monotonic()
                while time.monotonic() - started < 600:
                    snapshot = page.request.get(BASE + f'/api/sessions/{sid}/activity').json()
                    user = next((e for e in reversed(snapshot['transcript']) if e['t'] == 'user' and prompt in e.get('text', '')), None)
                    if snapshot.get('phase') == 'awaiting' and user and any(e['t'] == 'done' and e.get('turn_id') == user.get('turn_id') for e in snapshot['events']):
                        return snapshot
                    if snapshot.get('phase') == 'idle' and any(e['t'] == 'error' for e in snapshot['events']):
                        raise AssertionError([e for e in snapshot['events'] if e['t'] == 'error'])
                    page.wait_for_timeout(500)
                raise AssertionError('Test model wait exhausted')
            for index, (prompt, volume) in enumerate([
                ('Create a solid box 20 x 10 x 5 mm with a named height parameter. No research needed.', 1000),
                ('Correction: change ONLY the height to 7 mm. Keep 20 x 10 mm unchanged.', 1400)
            ]):
                if index:
                    # Reproduce the actual stop/resume bug, rather than only an active follow-up.
                    page.evaluate("""sid => new Promise(resolve => {const ws = new WebSocket(`ws://${location.host}/ws/agent/${sid}`); ws.onopen=()=>{ws.send(JSON.stringify({t:'stop'}));setTimeout(()=>{ws.close();resolve();},500);};})""", sid)
                page.fill('#composer-input', prompt); page.locator('#btn-send').click()
                snapshot = wait_turn(prompt)
                (output / f'activity-{index}.json').write_text(json.dumps(snapshot, indent=2))
                session = next(s for s in page.request.get(BASE + '/api/status').json()['sessions'] if s['id'] == sid)
                project = page.request.get(BASE + '/api/projects/' + session['project_id']).json()
                assert abs(project['geometry']['volume_mm3'] - volume) < .001, project['geometry']
                step = page.request.get(BASE + f'/api/projects/{project["id"]}/{project["head"]}/model.step')
                assert step.ok and len(step.body()) > 100
                (output / f'model-{index}.step').write_bytes(step.body())
                print(f'PASS: model turn {index + 1}, independently measured volume {volume}', flush=True)
            reference = Path('/tmp/cad-user-reference.jpg')
            if reference.exists():
                page.set_input_files('#attach-input', str(reference))
                expect(page.locator('#attachment-chips .attachment-chip')).to_have_count(1)
                prompt = 'Look at the attached board reference image. Identify which edge holds the microSD connector and which edge holds the mini HDMI and micro USB connectors, using image top/bottom/left/right. Do not change any geometry.'
                page.fill('#composer-input', prompt); page.locator('#btn-send').click()
                snapshot = wait_turn(prompt)
                (output / 'reference-activity.json').write_text(json.dumps(snapshot, indent=2))
                print('PASS: reference image delivered through upload, relay and model turn; response saved for review', flush=True)
            page.screenshot(path=str(output / 'studio.png'))
            page.reload(); expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            page.set_viewport_size({'width':390, 'height':844})
            page.screenshot(path=str(output / 'mobile-studio.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.goto(BASE + '/onboarding'); page.screenshot(path=str(output / 'mobile-onboarding.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            with (output / 'docker-reconnect.txt').open('w') as log:
                subprocess.run(commands + ['up', '-d', '--force-recreate', '--no-build', '--wait', 'cad'], check=True, stdout=log, stderr=log)
            expect(page.locator('#connection')).to_have_text('Connected', timeout=60000)
            assert page.request.get(BASE + '/api/projects/' + project['id']).json()['head'] == project['head']
            print('PASS: permanent worker credential reconnects after container recreation; project survives', flush=True)
            page.locator('#btn-logout').click(); page.wait_for_url(BASE + '/login')
            assert page.request.get(BASE + '/api/projects').status == 401
            assert not errors, errors
            (output / 'result.json').write_text(json.dumps({'success': True, 'browser_errors': errors}, indent=2))
            print('PASS: browser reload, mobile studio/onboarding, logout', flush=True)
        finally:
            if commands:
                with (output / 'docker-logs.txt').open('w') as log:
                    subprocess.run(commands + ['logs', '--tail=100'], stdout=log, stderr=log)
                with (output / 'docker-stop.txt').open('w') as log:
                    subprocess.run(commands + ['down'], stdout=log, stderr=log)
            browser.close()


if __name__ == '__main__':
    main()
