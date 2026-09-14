"""Controlled full-shell theme fixture: real UI, mocked backend, no CAD writes."""
import argparse
import io
import json
from pathlib import Path
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright, expect


def run(base):
    output = Path(tempfile.mkdtemp(prefix='workspace-theme-', dir=Path(__file__).resolve().parents[2]/'runs/harness-checks'))
    errors = []; requests = []; sessions = []
    apps = [{'id': 'freecad', 'name': 'FreeCAD', 'accent': '#e74c3c'}, {'id': 'openscad', 'name': 'OpenSCAD', 'accent': '#42c18d'}]
    project = {'id': 'fixture-project', 'name': 'Controlled fixture · Architectural enclosure', 'head': 'r0002',
               'app_id': 'freecad', 'revisions': [{'id': 'r0001', 'name': 'Base'}, {'id': 'r0002', 'name': 'Vented lid'}],
               'design': {'parameters': [{'name': 'length', 'value': 90}, {'name': 'width', 'value': 62},
                                         {'name': 'wall_thickness', 'value': 2}, {'name': 'slip_fit_clearance', 'value': 0.25}]},
               'geometry': {'valid_geometry': True, 'valid_solid': False, 'solid_count': 2,
                            'bounds_mm': [90, 62, 24], 'volume_mm3': 12000, 'cuts': [{}, {}]}}
    session = {'id': 'fixture-shell', 'app': apps[0], 'width': 1280, 'height': 900, 'app_alive': True,
               'agent_active': False, 'project_id': project['id'], 'engine': 'hybrid', 'native_state_available': True}
    frame = io.BytesIO(); Image.new('RGB', (1280, 900), '#30302e').save(frame, 'JPEG')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960}, color_scheme='light')
        page.on('pageerror', lambda error: errors.append(str(error)))
        def api(route):
            path = route.request.url.split('/api/', 1)[1]
            if route.request.method != 'GET':
                requests.append({'path': path, 'body': route.request.post_data_json})
            if path == 'auth/session':
                data = {'enabled': False, 'authenticated': True, 'username': None, 'registration': False, 'deployment': 'standalone'}
            elif path == 'status':
                data = {'missing_tools': [], 'sessions': sessions, 'policy': {'ok': True}, 'planner': {'ok': True},
                        'research': {'enabled': True}, 'supervision': {'native_operations': True}}
            elif path == 'apps': data = {'apps': apps}
            elif path == 'settings': data = {'version': 1, 'web_search': True, 'reasoning_effort': 'medium', 'active_model': 'fixture', 'efforts': ['low', 'medium', 'high', 'xhigh'],
                                             'models': [{'name': 'fixture', 'base_url': 'http://127.0.0.1:8000/v1', 'model': 'fixture', 'has_key': False, 'thinking_token_budget': None}]}
            elif path == 'projects': data = {'projects': [project]}
            elif path.startswith('projects/'): data = project
            elif path == 'sessions':
                sessions.append(session); data = session
            elif path.endswith('/project'):
                if route.request.post_data_json.get('operation') == 'engine':
                    session['engine'] = route.request.post_data_json['engine']
                data = project
            else:
                raise AssertionError('Unexpected fixture route: '+path)
            route.fulfill(json=data)
        page.route('**/api/**', api)
        page.route_web_socket('**/ws/view/*', lambda ws: ws.send(frame.getvalue()))
        page.route_web_socket('**/ws/agent/*', lambda ws: ws.send(json.dumps({'t': 'snapshot', 'events': [],
            'active': False, 'paused': False, 'mode': 'auto', 'phase': 'idle', 'web_enabled': True})))
        page.goto(base, wait_until='networkidle')
        def screenshot(name):
            page.wait_for_timeout(220); page.screenshot(path=str(output/name), full_page=True)
        def same_theme():
            colors = page.evaluate("['body','#topbar','#agent-panel','#stage'].map(s=>getComputedStyle(document.querySelector(s)).backgroundColor)")
            assert len(set(colors)) == 1, colors
            assert page.locator('#screen').evaluate('(el)=>getComputedStyle(el).filter') == 'none'
        expect(page.locator('.app-tile')).to_have_count(2)
        expect(page.locator('.recent-projects button')).to_have_count(1)
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        same_theme(); screenshot('01-launcher-dark.png')
        page.locator('#btn-theme').click(); same_theme(); screenshot('02-launcher-light.png')
        page.reload(wait_until='networkidle'); expect(page.locator('html')).to_have_attribute('data-theme', 'light')
        page.locator('#btn-help').click(); screenshot('03-shortcuts-light.png')
        page.keyboard.press('Escape'); expect(page.locator('#help-dialog')).to_be_hidden()
        page.locator('.recent-projects button').click()
        expect(page.locator('#connection-status')).to_have_text('Live')
        assert requests[0]['body']['project_id'] == project['id']
        expect(page.locator('#model-status')).to_have_text('✓ 2 valid parts')
        page.locator('#model-panel>summary').click()
        expect(page.locator('#model-downloads a')).to_have_count(5)
        expect(page.locator('#model-parameters span')).to_have_count(4)
        assert page.locator('#model-downloads a').first.get_attribute('href').endswith('/r0002/model.FCStd')
        page.locator('#engine-select').select_option('visual')
        expect(page.locator('.toast')).to_contain_text('Modeling tools updated')
        assert requests[-1]['body'] == {'operation': 'engine', 'engine': 'visual'}
        screenshot('04-model-and-tools-light.png')
        page.locator('#btn-theme').click(); same_theme(); screenshot('05-model-and-tools-dark.png')
        page.locator('#btn-close-session').click(); screenshot('06-session-dialog-dark.png')
        page.locator('#close-dialog button[value=cancel]').click()
        assert not any(r['path'] == 'sessions/fixture-shell' for r in requests)
        # System preference updates every app surface, not only the conversation.
        for width in (320, 390, 760, 1024, 1440):
            page.set_viewport_size({'width': width, 'height': 844})
            if width <= 760: page.locator('#btn-mobile-view').click()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width
            expect(page.locator('#model-panel')).to_be_in_viewport()
            assert page.locator('#model-panel').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            if width == 390: screenshot('07-mobile-model-dark.png')
            if width <= 760: page.locator('#btn-mobile-view').click()
        assert not errors, errors
        browser.close()
    report = {'status': 'passed', 'browser_errors': errors, 'requests': requests,
              'scope': 'Controlled backend fixtures; no CAD model or user session was changed.'}
    (output/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'artifacts': str(output), **report}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--base', default='http://127.0.0.1:7802')
    run(parser.parse_args().base)
