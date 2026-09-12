"""Controlled browser regression: advisory model checks must not block chat.

Serves the shipped UI with in-browser HTTP/WebSocket fixtures. Does not start
servers, change account settings, or send requests to a CAD/model endpoint.
"""
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright, expect

WEB = Path(__file__).resolve().parents[1] / 'web'


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for policy, planner, engine in ((False, True, 'hybrid'), (False, False, 'hybrid'), (False, False, 'visual')):
                context = browser.new_context()
                page = context.new_page()
                errors, commands = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                session = {'id': 'fixture', 'app': {'id': 'freecad', 'name': 'FreeCAD'},
                           'width': 1280, 'height': 900, 'app_alive': True, 'engine': engine}
                model = {'name': 'qwen', 'base_url': 'https://lm.gptbox.dev/v1', 'model': 'qwen3.8-27b', 'has_key': False}
                def serve(route):
                    path = urlsplit(route.request.url).path
                    if path == '/api/status':
                        data = {'sessions': [session], 'missing_tools': [], 'research': {'enabled': True},
                                'chat': {'runtime': 'pi-coding-agent@0.85.1'},
                                'policy': {'ok': policy}, 'planner': {**model, 'ok': planner}}
                    elif path == '/api/apps':
                        data = {'apps': [session['app']]}
                    elif path == '/api/projects':
                        data = {'projects': []}
                    elif path == '/api/auth/session':
                        data = {'enabled': False}
                    elif path == '/api/settings':
                        data = {'version': 1, 'web_search': True, 'reasoning_effort': 'medium',
                                'active_model': 'qwen', 'models': [model], 'efforts': ['low', 'medium', 'high']}
                    else:
                        file = WEB / ('index.html' if path == '/' else path.removeprefix('/static/'))
                        if file.is_file():
                            route.fulfill(path=str(file), headers={'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'"})
                        else:
                            route.fulfill(status=404, body='Not found')
                        return
                    route.fulfill(json=data)
                page.route('**/*', serve)
                def agent(socket):
                    socket.on_message(lambda message: commands.append(json.loads(message)))
                    socket.send(json.dumps({'t': 'snapshot', 'events': [], 'transcript': [],
                                            'active': False, 'paused': False, 'phase': 'idle', 'mode': 'auto',
                                            'task': '', 'completed_steps': 0, 'max_steps': 80, 'web_enabled': True}))
                page.route_web_socket('**/ws/agent/*', agent)
                frame = io.BytesIO()
                Image.new('RGB', (1280, 900), '#30302e').save(frame, 'JPEG')
                page.route_web_socket('**/ws/view/*', lambda socket: socket.send(frame.getvalue()))
                page.goto('https://model-health-fixture.test/')
                page.locator('.session-tab').first.click()
                expect(page.locator('#connection-status')).to_have_text('Live')
                expect(page.locator('#pill-planner')).to_have_text('Assistant')
                if engine == 'hybrid':
                    expect(page.locator('#pill-policy')).to_be_hidden()
                else:
                    expect(page.locator('#pill-policy')).to_be_visible()
                    expect(page.locator('#pill-policy')).to_have_text('Action model')
                message = 'Create a 20 x 10 x 5 mm box.'
                page.locator('#composer-input').fill(message)
                page.locator('#btn-send').click()
                expect(page.locator('.pending-user')).to_contain_text(message)
                assert any(c.get('t') == 'start' and c.get('task') == message for c in commands), commands
                assert not errors, errors
                print(f'PASS: {engine}, policy={policy}, assistant={planner}: message reaches agent; correct indicator shown')
                context.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
