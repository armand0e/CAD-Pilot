"""Opt-in real signup -> GitHub clone/build -> Docker pairing -> live CAD check.

Uses a new account and isolated checkout/volumes; requires a running portal, Git,
Docker, Playwright and an image/tool model reachable from the worker's defaults.
The test account remains on the portal, disconnected. All test containers are removed.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:7804')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    os.umask(0o077)
    artifacts = ROOT / 'runs/harness-checks'
    artifacts.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='github-', dir=artifacts))
    print(output, flush=True)
    wrapper = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base)
            page.locator('#auth-toggle').click()
            username = 'github-check-' + secrets.token_hex(4)
            (output / 'account.txt').write_text(username)
            page.fill('#username', username)
            page.fill('#password', secrets.token_urlsafe(24))
            page.locator('button[type=submit]').click()
            page.wait_for_url(base + '/onboarding')
            assert page.request.get(base + '/api/projects').status == 503
            page.locator('#generate').click()
            expect(page.locator('#command-panel')).to_be_visible()
            command = page.locator('#command').inner_text()
            assert 'raw.githubusercontent.com/armand0e/CAD-Pilot/main/install.sh' in command
            identity = shlex.split(command)[-2]
            repo = output / 'CAD Pilot'
            wrapper = repo / 'harness/.docker/workers' / identity / 'cadpilot'
            # Secrets are not written to screenshots or the command log.
            page.locator('#copy-command').click()
            expect(page.locator('#copy-command')).to_have_text('Copied')
            with (output / 'install.log').open('w') as log:
                subprocess.run(['bash', '-c', command], env=dict(os.environ, CADPILOT_INSTALL_DIR=str(repo)),
                               check=True, stdout=log, stderr=log)
            expect(page.locator('#connection')).to_have_text('Connected', timeout=60000)
            print('PASS: public GitHub download, fresh clone, real Docker build, account pairing', flush=True)
            compose = json.loads(subprocess.check_output([str(wrapper), 'config', '--format', 'json']))
            assert all(not service.get('ports') for service in compose['services'].values())
            health = json.loads(subprocess.check_output([str(wrapper), 'exec', '-T', 'cad', 'python', '-m', 'server.connector']))
            assert health['ok'] and health['connector']['state'] == 'connected'
            status = page.request.get(base + '/api/status').json()
            assert status['chat']['runtime'] == 'pi-coding-agent@0.85.1'
            apps = page.request.get(base + '/api/apps').json()['apps']
            assert len([app for app in apps if 'openscad' in app['name'].lower()]) == 1
            page.locator('#enter').click()
            page.wait_for_url(base + '/')
            page.locator('.app-tile', has_text='FreeCAD').click()
            expect(page.locator('#connection-status')).to_have_text('Live', timeout=30000)
            page.screenshot(path=str(output / 'studio.png'))
            page.goto(base + '/onboarding')
            # Command is kept only in this page's memory, never localStorage.
            expect(page.locator('#command-panel')).to_be_hidden()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'onboarding-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            with (output / 'reconnect.log').open('w') as log:
                subprocess.run([str(wrapper), 'up', '-d', '--force-recreate', '--no-build', '--wait', 'cad'],
                               check=True, stdout=log, stderr=log)
            expect(page.locator('#connection')).to_have_text('Connected', timeout=60000)
            print('PASS: real Pi runtime, one OpenSCAD entry, live FreeCAD, mobile onboarding, permanent credential reconnect', flush=True)
            page.locator('#disconnect').click()
            expect(page.locator('#connection')).to_have_text('Waiting for your computer')
            page.locator('#btn-logout').click()
            page.wait_for_url(base + '/login')
            assert not errors, errors
            (output / 'result.json').write_text(json.dumps({'success': True, 'browser_errors': errors}, indent=2))
        finally:
            if wrapper and wrapper.exists():
                with (output / 'cleanup.log').open('w') as log:
                    subprocess.run([str(wrapper), 'logs', '--tail=60'], stdout=log, stderr=log)
                    subprocess.run([str(wrapper), 'down', '-v'], stdout=log, stderr=log)
            browser.close()


if __name__ == '__main__':
    main()
