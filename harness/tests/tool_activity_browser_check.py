"""Check streamed tool identity, parameter labels, and reconnect replay in the UI.

Uses the shipped chat modules with local browser fixtures; no running server,
account, CAD document, or model endpoint is needed.
"""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

WEB = Path(__file__).resolve().parents[1] / 'web'


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            pixel = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c636000020000050001e2265c8f0000000049454e44ae426082')

            def serve(route):
                path = urlsplit(route.request.url).path
                if path == '/':
                    route.fulfill(content_type='text/html', body='<div id="chat"></div><button id="jump"></button><div id="announce"></div>')
                elif path.startswith('/api/projects/'):
                    route.fulfill(content_type='image/png', body=pixel)
                else:
                    route.fulfill(path=str(WEB / path.removeprefix('/static/')))

            page.route('**/*', serve)
            page.goto('https://tool-activity-fixture.test/')
            page.evaluate("""async () => {
                const {ChatPanel}=await import('/static/chat/panel.js');
                window.panel=new ChatPanel(document.querySelector('#chat'),document.querySelector('#jump'),document.querySelector('#announce'));
                window.events=[];
                window.emit=event=>{
                    event={id:events.length+1,turn_id:'parameters',ts:100+events.length,...event};
                    events.push(event);panel.consume(event);
                };
                emit({t:'user',text:'Create an enclosure with editable parameters.'});
            }""")

            def emit(kind, **fields):
                page.evaluate('event=>emit(event)', {'t': kind, **fields})

            for index, (name, value) in enumerate((('wall', 2), ('case_l', 'board_l + 2*wall'), ('offset', 0))):
                operation_id = f'parameter-{index}'
                row = page.locator(f'[data-operation-id="{operation_id}"]')
                emit('tool_input_start', operation_id=operation_id)
                if index == 0:
                    emit('tool_input_delta', operation_id=operation_id, tool='define_parameter', offset=0, text='{"name":"wall","value":2}')
                # The final event must supply the label even without any deltas.
                emit('tool_input_done', operation_id=operation_id, tool='define_parameter', arguments={'name': name, 'value': value})
                expect(row.locator('.activity-label')).to_have_text(f'Preparing define parameter · {name} = {value}')
                expect(row.locator('.activity-status')).to_have_text('Validating input')
                emit('tool_settled', operation_id=operation_id, status='completed', message='Recorded in the workspace.')
                # Python completion plus Pi tool_execution_end use the same ID.
                emit('tool_settled', operation_id=operation_id, status='completed')
                expect(row).to_have_count(1)
                expect(row.locator('.activity-label')).to_have_text(f'Defined parameter · {name} = {value}')
                expect(row.locator('.activity-summary')).to_have_text('Recorded in the workspace.')
            # A tool that returned a picture to the model shows it inline, only from a same-origin project image URL.
            emit('tool_input_start', operation_id='preview-1')
            emit('tool_input_done', operation_id='preview-1', tool='path_preview', arguments={'path': 'M0 0 H10 V5 Z'})
            emit('tool_image', operation_id='preview-1', image='0123456789abcdef.jpg', label='Path preview: bracket', url='/api/projects/0123456789abcdef/images/0123456789abcdef.jpg')
            emit('tool_image', operation_id='preview-1', image='evil.jpg', label='x', url='https://evil.example/steal.jpg')
            emit('tool_settled', operation_id='preview-1', status='completed', message='')
            preview = page.locator('[data-operation-id="preview-1"] .activity-image')
            expect(preview).to_have_count(1)
            expect(preview.locator('img')).to_have_attribute('src', '/api/projects/0123456789abcdef/images/0123456789abcdef.jpg')
            expect(preview).to_have_attribute('href', '/api/projects/0123456789abcdef/images/0123456789abcdef.jpg')

            for status in ('failed', 'cancelled'):
                emit('tool_input_start', operation_id=status)
                emit('tool_input_done', operation_id=status, tool='define_parameter', arguments={'name': 'wall', 'value': -2})
                emit('tool_settled', operation_id=status, status=status, message='Unable to save parameter.')
                emit('tool_settled', operation_id=status, status='completed')
                row = page.locator(f'[data-operation-id="{status}"]')
                expect(row).to_have_attribute('data-status', status)
                expect(row.locator('.activity-label')).to_have_text('Define parameter · wall = -2')

            # A parameter edit with existing geometry becomes the same CAD row.
            emit('tool_input_start', operation_id='edit')
            emit('tool_input_done', operation_id='edit', tool='define_parameter', arguments={'name': 'wall', 'value': 3})
            page.evaluate('window.originalRow=document.querySelector("[data-operation-id=edit]")')
            emit('intent', operation_id='edit', step=1, tool='native_model', text='Define parameter · wall', arguments={'name': 'wall', 'value': 3})
            emit('step_done', operation_id='edit', step=1, verification_scope='Fixture geometry')
            emit('tool_settled', operation_id='edit', status='completed')
            assert page.evaluate('originalRow===document.querySelector("[data-operation-id=edit]")')
            expect(page.locator('[data-operation-id="edit"] .activity-status')).to_have_text('Saved · checked geometry')

            live = page.locator('.activity-label').all_text_contents()
            page.evaluate('panel.replay(events,true);panel.replay(events,true)')
            assert page.locator('.activity-label').all_text_contents() == live
            events = page.evaluate('events')
            page.reload()
            page.evaluate("""async events=>{
                const {ChatPanel}=await import('/static/chat/panel.js');
                const panel=new ChatPanel(document.querySelector('#chat'),document.querySelector('#jump'),document.querySelector('#announce'));
                panel.replay(events,false);
            }""", events)
            assert page.locator('.activity-label').all_text_contents() == live
            assert not errors, errors
            print('PASS: distinct parameter labels, duplicate completion, failure/cancellation, CAD row identity, and history replay')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
