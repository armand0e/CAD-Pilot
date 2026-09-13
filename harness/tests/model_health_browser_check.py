"""Controlled browser regression: advisory model checks must not block chat.

Serves the shipped UI with in-browser HTTP/WebSocket fixtures. Does not start
servers, change account settings, or send requests to a CAD/model endpoint.
"""
import io
import json
import time
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
                errors, commands, sockets, events = [], [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                session = {'id': 'fixture', 'app': {'id': 'freecad', 'name': 'FreeCAD'},
                           'width': 1280, 'height': 900, 'app_alive': True, 'engine': engine}
                model = {'name': 'qwen', 'base_url': 'https://lm.gptbox.dev/v1', 'model': 'qwen3.8-27b', 'has_key': False}
                settings = {'version': 1, 'web_search': True, 'reasoning_effort': 'medium',
                            'active_model': 'qwen', 'models': [model], 'efforts': ['off', 'low', 'medium', 'xhigh']}
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
                        if route.request.method == 'PUT':
                            settings.update(json.loads(route.request.post_data))
                        data = settings
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
                    sockets.append(socket)
                    socket.on_message(lambda message: commands.append(json.loads(message)))
                    socket.send(json.dumps({'t': 'snapshot', 'events': events, 'transcript': events,
                                            'active': False, 'paused': False, 'phase': 'idle', 'mode': 'auto',
                                            'task': '', 'completed_steps': 0, 'max_steps': 80, 'web_enabled': True}))
                page.route_web_socket('**/ws/agent/*', agent)
                frame = io.BytesIO()
                Image.new('RGB', (1280, 900), '#30302e').save(frame, 'JPEG')
                page.route_web_socket('**/ws/view/*', lambda socket: socket.send(frame.getvalue()))
                page.goto('https://model-health-fixture.test/')
                page.locator('.session-tab').first.click()
                expect(page.locator('#connection-status')).to_have_text('Live')
                page.locator('#btn-settings').click()
                image_limit = page.get_by_role('spinbutton', name='Images per request (endpoint limit; default 16)', exact=True)
                image_limit.fill('3')
                page.locator('#settings-save').click()
                expect(page.locator('#settings-dialog')).not_to_be_visible()
                assert settings['models'][0]['max_images_per_request'] == 3
                expect(page.locator('#pill-planner')).to_have_text('Assistant')
                page.locator('#model-effort-label').click()
                page.locator('#menu-effort').get_by_text('Off', exact=True).click()
                expect(page.locator('#model-effort-label')).to_have_text('Off')
                assert settings['reasoning_effort'] == 'off'
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
                def emit(kind, turn, **fields):
                    event = {'t': kind, 'turn_id': turn, 'id': len(events) + 1, 'ts': time.time(), **fields}
                    events.append(event)
                    sockets[-1].send(json.dumps(event))
                emit('image_context', 'images', limit=3, reference_total=6, reference_omitted=3, timeline=False)
                expect(page.locator('#image-context-notice')).to_contain_text('Viewing 3 of 6 reference images')
                expect(page.locator('[data-turn-id="images"]')).to_have_count(0)
                emit('image_context', 'images', limit=3, reference_total=2, reference_omitted=0, timeline=False)
                expect(page.locator('#image-context-notice')).to_be_hidden()
                reason = 'Reply sent; the model stays open for more changes.'
                emit('user', 'failed', text=message)
                emit('error', 'failed', message='502 status code (no body)')
                emit('done', 'failed', reason=reason)
                failed = page.locator('[data-turn-id="failed"]')
                expect(failed).to_have_attribute('data-status', 'failed')
                expect(failed.locator('.turn-footer')).to_contain_text('502')
                emit('user', 'empty', text='Continue')
                emit('answer_start', 'empty', answer_id='earlier-reply')
                emit('answer_delta', 'empty', answer_id='earlier-reply', offset=0, text='I will prepare the enclosure.')
                emit('answer_done', 'empty', answer_id='earlier-reply', status='completed')
                emit('thinking_start', 'empty', operation_id='thinking-empty')
                emit('thinking_delta', 'empty', operation_id='thinking-empty', text='Preparing an enclosure.')
                emit('thinking_done', 'empty', operation_id='thinking-empty', status='completed')
                emit('done', 'empty', reason=reason)
                empty = page.locator('[data-turn-id="empty"]')
                expect(empty).to_have_attribute('data-status', 'interrupted')
                expect(empty.locator('.turn-footer')).to_contain_text('without an answer')
                emit('user', 'success', text='Continue again')
                emit('answer_start', 'success', answer_id='reply')
                emit('answer_delta', 'success', answer_id='reply', offset=0, text='The enclosure is ready.')
                emit('answer_done', 'success', answer_id='reply', status='completed')
                emit('done', 'success', reason=reason)
                expect(page.locator('[data-turn-id="success"]')).to_have_attribute('data-status', 'completed')
                page.reload()
                expect(failed).to_have_attribute('data-status', 'failed')
                expect(failed.locator('.turn-footer')).to_contain_text('502')
                expect(empty).to_have_attribute('data-status', 'interrupted')
                expect(empty.locator('.turn-footer')).to_contain_text('without an answer')
                assert not errors, errors
                print(f'PASS: {engine}, policy={policy}, assistant={planner}: sending, indicators, failure/truncation status and replay')
                context.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
