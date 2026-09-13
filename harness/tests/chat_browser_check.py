"""Real app + controlled WebSocket stream, no CAD/model mutations or live sources.

Only backend routes are replaced. Exercises the shipped modules/styles under the
application's real CSP. Evidence is explicitly labeled as a controlled fixture.
"""
import argparse
import io
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright, expect


def run(base):
    artifacts=Path(tempfile.mkdtemp(prefix='chat-ui-',dir=Path(__file__).resolve().parents[2]/'runs/harness-checks'))
    errors=[];events=[];commands=[];sockets=[];active=False;started_at=time.time()
    session={'id':'fixture-chat','app':{'id':'freecad','name':'FreeCAD','accent':'#b78766'},'width':1280,'height':900,'app_alive':True,'engine':'visual'}
    frame=io.BytesIO();Image.new('RGB',(1280,900),'#30302e').save(frame,'JPEG')
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':960},color_scheme='light')
        page.on('pageerror',lambda e:errors.append(str(e)))
        def static(route):
            path=urlsplit(route.request.url).path
            file=Path(__file__).resolve().parents[1]/'web'/('index.html' if path=='/' else path.removeprefix('/static/'))
            if file.is_file():route.fulfill(path=str(file),headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'"})
            else:route.fulfill(status=404,body='Not found')
        page.route('**/*',static)
        page.route('**/api/auth/session',lambda r:r.fulfill(json={'enabled':False}))
        def instrument(route):
            source=(Path(__file__).resolve().parents[1]/'web/app.js').read_text()
            route.fulfill(content_type='text/javascript',body=source+"\nwindow.__chatTest={event:handleAgentEvent,panel:chatPanel}; document.querySelector('.brand-tag').textContent='CONTROLLED FIXTURE';\n")
        page.route('**/static/app.js',instrument)
        page.route('**/api/status',lambda r:r.fulfill(json={'missing_tools':[],'sessions':[session],
            'policy':{'ok':True},'planner':{'ok':True},'research':{'enabled':True}}))
        page.route('**/api/apps',lambda r:r.fulfill(json={'apps':[session['app']]}))
        page.route('**/api/projects',lambda r:r.fulfill(json={'projects':[]}))
        # Settings are served and accepted by the fixture so the check never edits the user's real settings.
        fixture_settings={'version':1,'web_search':True,'reasoning_effort':'medium','active_model':'fixture','efforts':['low','medium','high','xhigh'],
            'models':[{'name':'fixture','base_url':'http://127.0.0.1:8000/v1','model':'fixture','has_key':False,'thinking_token_budget':None}]}
        def settings_route(route):
            if route.request.method=='PUT':
                body=json.loads(route.request.post_data or '{}');fixture_settings.update({k:body[k] for k in ('web_search','reasoning_effort','active_model') if k in body})
            route.fulfill(json=fixture_settings)
        page.route('**/api/settings',settings_route)
        def snapshot():return {'t':'snapshot','events':events,'transcript':events,'chat_protocol':1,'web_enabled':True,
            'active':active,'paused':False,'mode':'auto','task':'Design a ventilated enclosure' if events else '',
            'phase':'planning' if active else 'idle','completed_steps':0,'max_steps':80,'started_at':started_at}
        def agent(ws):
            sockets.append(ws);ws.send(json.dumps(snapshot()));ws.on_message(lambda m:commands.append(json.loads(m)))
        page.route_web_socket('**/ws/agent/*',agent)
        page.route_web_socket('**/ws/view/*',lambda ws:ws.send(frame.getvalue()))
        page.goto(base,wait_until='networkidle')
        # Dark is the default even on a light OS. Light remains an explicit,
        # persisted choice for this fixture's first set of screenshots.
        expect(page.locator('html')).to_have_attribute('data-theme','dark')
        page.locator('#btn-theme').click();expect(page.locator('html')).to_have_attribute('data-theme','light')
        page.locator('.session-tab').first.click()
        expect(page.locator('#connection-status')).to_have_text('Live')
        def emit(t,**fields):
            event={'t':t,'turn_id':'turn-one','ts':time.time(),'id':len(events)+1,'event_id':f'fixture-event-{len(events)+1}',**fields}
            events.append(event);sockets[-1].send(json.dumps(event));page.wait_for_timeout(18);return event
        def screenshot(name):
            page.wait_for_timeout(350);page.screenshot(path=str(artifacts/name),full_page=True)
            page.locator('#agent-panel').screenshot(path=str(artifacts/('panel-'+name)))
        query='Raspberry Pi 3 mechanical drawing and connector layout'
        titles=['Raspberry Pi 3 Model B — mechanical drawing and connector keep-out dimensions',
                'Raspberry Pi hardware documentation','Board mounting and enclosure design','Raspberry Pi 3 product brief',
                'Enclosure clearance considerations','Board drawings and CAD files','Ventilation and thermal design','Raspberry Pi 3 Model B+ mechanical drawing']
        domains=['raspberrypi.com','raspberrypi.com','example.com','pip.raspberrypi.com','example.org','raspberrypi.com','example.net','pip.raspberrypi.com']
        sources=[{'id':f'web_{i+1:012x}','url':f'https://{domains[i]}/fixture/{i}', 'domain':domains[i],
            'title':title,'snippet':'Controlled fixture snippet: board dimensions and connector clearances are described in this source. Confirm the exact board variant.',
            'kind':'search_result','opened':False,'retrievedAt':'2026-09-11T08:00:06Z',
            **({'published_at':'2024-02-14','source_type':'official'} if i==0 else {})} for i,title in enumerate(titles)]
        sources[0]['favicon']='/static/missing-fixture.png'
        page.locator('#composer-input').fill('Make a distinctive, ventilated Raspberry Pi 3 enclosure.')
        page.locator('#btn-send').click()
        expect(page.locator('.pending-user')).to_contain_text('ventilated')
        assert page.locator('#composer-input').input_value()==''
        assert commands[-1]['t']=='start'
        active=True
        emit('user',text='Make a distinctive, ventilated Raspberry Pi 3 enclosure.')
        emit('control',locked=True,mode='auto',task='Design a ventilated enclosure')
        emit('thinking_start',operation_id='thinking-one',text='')
        emit('thinking_delta',operation_id='thinking-one',text='I’ll first look for the exact board variant and its mechanical drawing. Then I can separate sourced dimensions from the design choices. This is a supplied display-summary fixture, not hidden reasoning.')
        expect(page.locator('#btn-send')).to_have_attribute('data-mode','stop')
        expect(page.locator('#composer-input')).to_be_editable()
        screenshot('01-thinking-light.png')
        emit('thinking_done',operation_id='thinking-one')
        emit('research_start',operation_id='search-one',operation='search',query=query,call=1)
        expect(page.locator('[data-operation-id="search-one"]')).to_contain_text('Searching the web')
        page.evaluate("window.__stableSearch=document.querySelector('[data-operation-id=search-one]')")
        emit('research_result',operation_id='search-one',operation='search',query=query,provider='bing',sources=sources,
             warnings=['duckduckgo · attempt 1: HTTP 403','duckduckgo · attempt 2: HTTP 403'])
        expect(page.locator('.source-row')).to_have_count(8)
        # Missing favicon keeps its dimensions and never injects a third-party beacon.
        assert page.locator('.source-row').first.locator('.source-icon').evaluate('(el)=>Math.round(el.getBoundingClientRect().width)')==12
        assert page.locator('.source-row img').count()==0
        assert page.evaluate("window.__stableSearch===document.querySelector('[data-operation-id=search-one]')")
        expect(page.locator('[data-operation-id="search-one"] .activity-status')).to_have_text('8 results')
        assert page.locator('.search-results:not([hidden])').evaluate('(el)=>el.scrollHeight>el.clientHeight')
        screenshot('02-eight-sources-light.png')
        page.locator('.source-info').first.focus();page.keyboard.press('Enter')
        expect(page.locator('.source-detail').first).to_contain_text('Discovered · not opened')
        expect(page.locator('.source-detail').first).to_contain_text('Retrieved')
        # Focus/disclosure does not get closed by an incoming visit.
        emit('research_start',operation_id='read-one',operation='read',query=sources[0]['url'],call=2)
        expect(page.locator('.source-info').first).to_have_attribute('aria-expanded','true')
        screenshot('03-fetching-and-source-detail.png')
        opened=sources[0]|{'kind':'page','opened':True,'title':'Raspberry Pi 3 Model B mechanical drawing',
                         'excerpt':'Controlled fixture extraction. Board and connector measurements are supplied here as source data, not instructions.',
                         'retrievedAt':'2026-09-11T08:00:12Z'}
        emit('research_result',operation_id='read-one',operation='read',sources=[opened])
        expect(page.locator('.source-detail').first).to_contain_text('Opened')
        emit('research_start',operation_id='read-two',operation='read',query=sources[1]['url'],call=3)
        emit('research_result',operation_id='read-two',operation='read',sources=[sources[1]|{'kind':'page','opened':True,'excerpt':'Controlled fixture: documentation extraction.'}])
        expect(page.locator('.timeline-toggle')).to_contain_text('4 steps')
        # A deliberately open older result remains visible after collapse-to-two.
        expect(page.locator('[data-operation-id="search-one"]')).to_be_visible()
        page.locator('.timeline-toggle').click()
        expect(page.locator('.timeline-toggle')).to_have_text('Hide steps')
        emit('answer_start',answer_id='answer-one')
        answer='A quiet, architectural case — with a **floating lid**, a recessed perimeter, and a band of angled ventilation slots.\n\nI found the board drawing and hardware documentation. The drawing is opened; the other search results remain leads. [web_000000000001]\n\nBefore modeling, I’ll confirm the port clearances against your exact Pi 3 variant. The CAD geometry check will not, by itself, certify mechanical fit.'
        for offset in range(0,len(answer),22):emit('answer_delta',answer_id='answer-one',offset=offset,text=answer[offset:offset+22])
        expect(page.locator('.citation')).to_have_count(1)
        page.locator('.citation').click()
        expect(page.locator('.citation-preview')).to_contain_text('Opened')
        assert page.locator('.citation-preview a').get_attribute('rel')=='noopener noreferrer'
        page.locator('.source-close').click()
        emit('answer_done',answer_id='answer-one',status='completed')
        emit('done',reason='Response ready for your review.')
        emit('control',locked=False);active=False
        expect(page.locator('#btn-send')).to_have_attribute('data-mode','send')
        if page.locator('#jump-latest').is_visible():page.locator('#jump-latest').click()
        screenshot('04-final-answer-light.png')
        # Replay duplicates must retain object identity, source expansion and citations.
        count=page.locator('.timeline-entry').count()
        page.evaluate('event=>window.__chatTest.event(event)',snapshot())
        assert page.locator('.timeline-entry').count()==count
        assert page.evaluate("window.__stableSearch===document.querySelector('[data-operation-id=search-one]')")
        expect(page.locator('.source-info').first).to_have_attribute('aria-expanded','true')
        # Source identities and full timeline survive a genuine page reload.
        page.reload(wait_until='networkidle')
        expect(page.locator('.citation')).to_have_count(1)
        assert page.locator('.timeline-entry').count()==count
        page.locator('#btn-theme').click();expect(page.locator('html')).to_have_attribute('data-theme','dark')
        screenshot('05-final-answer-dark.png')
        # Real UI-state regressions for late events and out-of-order offsets.
        emit('user',turn_id='turn-two',text='Show me the source failures and keep the successful results.')
        emit('control',turn_id='turn-two',locked=True,mode='auto',task='Review sources');active=True
        emit('research_start',turn_id='turn-two',operation_id='search-two',query='enclosure manufacturing guidance',call=4)
        emit('research_error',turn_id='turn-two',operation_id='search-two',code='search_unavailable',message='Search engines returned no results or an access challenge.',diagnostics=[{'engine':'duckduckgo','attempt':1,'code':'challenge'}])
        emit('research_start',turn_id='turn-two',operation_id='read-failed',operation='read',query='https://example.com/unavailable',call=5)
        emit('research_error',turn_id='turn-two',operation_id='read-failed',message='Website returned HTTP 403',code='http_error')
        emit('research_start',turn_id='turn-two',operation_id='read-cancelled',operation='read',query='https://example.org/slow',call=6)
        emit('research_cancelled',turn_id='turn-two',operation_id='read-cancelled',message='Lookup cancelled.')
        emit('research_result',turn_id='turn-two',operation_id='read-cancelled',operation='read',sources=[opened])
        expect(page.locator('[data-operation-id="read-cancelled"]')).to_have_attribute('data-status','cancelled')
        expect(page.locator('[data-operation-id="read-failed"]')).to_contain_text('Failed')
        emit('thinking_start',turn_id='turn-two',operation_id='long-thinking',text='Supplied display summary.\n\n'*90)
        page.locator('[data-operation-id="long-thinking"] .activity-heading').click()
        page.locator('.thinking-more').last.click()
        expect(page.locator('.thinking-more').last).to_have_text('Show less')
        emit('thinking_done',turn_id='turn-two',operation_id='long-thinking')
        emit('answer_start',turn_id='turn-two',answer_id='answer-two')
        emit('answer_delta',turn_id='turn-two',answer_id='answer-two',offset=6,text='world')
        emit('answer_delta',turn_id='turn-two',answer_id='answer-two',offset=0,text='Hello ')
        expect(page.locator('.answer-body').last).to_have_text('Hello world')
        # Preserve selection + scrollTop as answer text arrives below the reader.
        page.locator('#chat').evaluate('(el)=>{el.scrollTop=0;el.dispatchEvent(new Event("scroll"));}')
        page.evaluate('''()=>{const node=document.querySelector('.answer-body span');window.__answerNode=node;
            const r=document.createRange();r.selectNodeContents(node);const s=getSelection();s.removeAllRanges();s.addRange(r);}''')
        selection=page.evaluate('getSelection().toString()')
        before=page.locator('#chat').evaluate('(el)=>el.scrollTop')
        long_text='\n\nA long streamed paragraph below your reading position. '*100
        emit('answer_delta',turn_id='turn-two',answer_id='answer-two',offset=11,text=long_text)
        assert page.locator('#chat').evaluate('(el)=>el.scrollTop')==before
        assert page.evaluate('getSelection().toString()')==selection
        assert page.evaluate('window.__answerNode===document.querySelector(".answer-body span")')
        expect(page.locator('#jump-latest')).to_be_visible()
        page.locator('#jump-latest').click()
        emit('answer_delta',turn_id='turn-two',answer_id='answer-two',offset=11+len(long_text),text='\nA discovered lead [web_000000000003]. Unknown reference [web_ffffffffffff].')
        expect(page.locator('.citation')).to_have_count(2)
        expect(page.locator('.answer-body').last).to_contain_text('[web_ffffffffffff]')
        page.locator('.citation').last.click()
        expect(page.locator('.citation-preview').last).to_contain_text('This citation used a search result')
        command_count=len(commands);page.keyboard.press('Escape')
        expect(page.locator('.citation-preview').last).to_be_hidden()
        assert len(commands)==command_count, 'Closing a source preview must not stop the agent'
        page.locator('#btn-send[data-mode="stop"]').click();assert commands[-1]['t']=='stop'
        emit('answer_done',turn_id='turn-two',answer_id='answer-two',status='cancelled')
        emit('done',turn_id='turn-two',reason='stopped by user');emit('control',turn_id='turn-two',locked=False);active=False
        page.locator('#btn-attach').click();page.locator('#menu-attach .switch').click();page.wait_for_timeout(60)
        assert commands[-1]=={'t':'web_setting','enabled':False},commands[-1]
        expect(page.locator('#web-search-toggle')).not_to_be_checked();page.keyboard.press('Escape')
        emit('web_setting',enabled=False)
        page.evaluate("document.documentElement.dataset.motion='reduced'")
        assert page.locator('.timeline-entry').first.evaluate('(el)=>getComputedStyle(el).animationName')=='none'
        expect(page.locator('.source-row')).to_have_count(8)
        page.emulate_media(reduced_motion='reduce')
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('#composer-input')).to_be_in_viewport()
        page.locator('#btn-mobile-view').click();expect(page.locator('#stage')).to_be_visible()
        page.locator('#btn-mobile-view').click();expect(page.locator('#composer-input')).to_be_in_viewport()
        page.locator('#chat').evaluate('(el)=>{el.scrollTop=0;el.dispatchEvent(new Event("scroll"));}')
        page.locator('.timeline-toggle').first.click()
        page.locator('.source-info').first.click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        screenshot('06-mobile-source-details.png')
        # Live provider summaries and schema-constrained CAD tool inputs.
        page.set_viewport_size({'width':1440,'height':960})
        emit('user',turn_id='turn-streaming',text='Use the provisional connector dimensions and build the case.')
        emit('control',turn_id='turn-streaming',locked=True,mode='auto')
        emit('phase',turn_id='turn-streaming',phase='planning',completed_steps=0,timeline=False)
        emit('thinking_start',turn_id='turn-streaming',operation_id='reasoning-live',request_id='request-live',label='Thinking…')
        empty=page.locator('[data-operation-id="reasoning-live"]')
        expect(empty.locator('.activity-heading')).to_be_disabled()
        assert 'No reasoning text' not in page.locator('#chat').inner_text()
        reasoning='The earlier answer approved approximate connector dimensions. I can preserve those as provisional and choose editable parameters for the base.'
        emit('thinking_delta',turn_id='turn-streaming',operation_id='reasoning-live',offset=0,text=reasoning[:55])
        expect(empty.locator('.activity-heading')).to_have_attribute('aria-expanded','true')
        node=empty.locator('.thinking-preview').evaluate('(el)=>{window.__reasoningNode=el.firstChild;return !!el.firstChild}')
        assert node
        # Deliberately close. Later deltas never force it open again.
        empty.locator('.activity-heading').click()
        emit('thinking_delta',turn_id='turn-streaming',operation_id='reasoning-live',offset=55,text=reasoning[55:])
        expect(empty.locator('.activity-heading')).to_have_attribute('aria-expanded','false')
        assert empty.locator('.thinking-preview').evaluate('(el)=>el.firstChild===window.__reasoningNode')
        emit('thinking_done',turn_id='turn-streaming',operation_id='reasoning-live',status='completed')
        emit('tool_input_start',turn_id='turn-streaming',operation_id='native-stream',request_id='request-live')
        proposal=page.locator('[data-operation-id="native-stream"]')
        raw='{"tool":"create_body","arguments":{"id":"case_base","name":"Provisional enclosure base"}}'
        for offset in range(0,len(raw),16):emit('tool_input_delta',turn_id='turn-streaming',operation_id='native-stream',offset=offset,text=raw[offset:offset+16],tool='create_body')
        expect(proposal.locator('.activity-status')).to_have_text('Drafting')
        expect(proposal).to_contain_text('nothing executed yet')
        proposal.locator('.activity-heading').click()
        expect(proposal.locator('.activity-raw')).to_have_text(raw)
        page.evaluate('window.__proposal=document.querySelector("[data-operation-id=native-stream]")')
        if page.locator('#jump-latest').is_visible():page.locator('#jump-latest').click()
        screenshot('07-live-tool-input-dark.png')
        emit('tool_input_done',turn_id='turn-streaming',operation_id='native-stream')
        expect(proposal.locator('.activity-status')).to_have_text('Validating input')
        emit('intent',turn_id='turn-streaming',operation_id='native-stream',step=1,text='Create body · case_base',tool='native_model')
        assert page.evaluate('window.__proposal===document.querySelector("[data-operation-id=native-stream]")')
        expect(proposal).to_have_attribute('data-status','running')
        expect(proposal.locator('.activity-heading')).to_have_attribute('aria-expanded','true')
        emit('step_done',turn_id='turn-streaming',operation_id='native-stream',step=1,verified=True,verification_scope='Controlled geometry fixture')
        expect(proposal.locator('.activity-status')).to_have_text('Saved · checked geometry')
        emit('tool_input_start',turn_id='turn-streaming',operation_id='cancel-draft')
        emit('tool_input_delta',turn_id='turn-streaming',operation_id='cancel-draft',offset=0,text='{"tool":"hollow"',tool='hollow')
        emit('tool_settled',turn_id='turn-streaming',operation_id='cancel-draft',status='cancelled',message='Stopped by user')
        emit('intent',turn_id='turn-streaming',operation_id='cancel-draft',step=2,text='Late call must not execute')
        expect(page.locator('[data-operation-id="cancel-draft"]')).to_have_attribute('data-status','cancelled')
        emit('done',turn_id='turn-streaming',reason='Stopped by user');active=False
        # Bounded local scrolling, soft append-only reveal and hover-only chrome.
        page.emulate_media(reduced_motion='no-preference')
        page.evaluate("document.documentElement.dataset.motion='system'")
        emit('user',turn_id='turn-thinking',text='Controlled fixture: stream a long displayable reasoning summary.')
        emit('control',turn_id='turn-thinking',locked=True,mode='auto');active=True
        emit('thinking_start',turn_id='turn-thinking',operation_id='scroll-thinking',request_id='scroll-request')
        thought=''.join(f'Fixture line {i}: checking an editable dimension against the requested design.\n' for i in range(50))
        emit('thinking_delta',turn_id='turn-thinking',operation_id='scroll-thinking',offset=0,text=thought)
        row=page.locator('[data-operation-id="scroll-thinking"]')
        region=row.locator('.thinking-preview')
        page.wait_for_timeout(350)
        if page.locator('#jump-latest').is_visible(): page.locator('#jump-latest').click()
        page.wait_for_timeout(50)
        assert region.evaluate('(el)=>el.clientHeight===200 && el.scrollHeight>el.clientHeight')
        assert region.evaluate('(el)=>el.scrollHeight-el.scrollTop-el.clientHeight<3')
        region.evaluate('(el)=>window.__firstThinkingChunk=el.firstChild')
        for i in range(3):
            delta=f'Newly streamed fixture line {50+i}: checking the next parameter.\n'
            emit('thinking_delta',turn_id='turn-thinking',operation_id='scroll-thinking',offset=len(thought),text=delta);thought+=delta
            page.wait_for_timeout(25)
            assert region.evaluate('(el)=>el.scrollHeight-el.scrollTop-el.clientHeight<3')
        assert region.evaluate('(el)=>el.firstChild===window.__firstThinkingChunk')
        assert region.locator('.thinking-chunk').last.evaluate('(el)=>getComputedStyle(el).animationName')=='thinking-arrive'
        # Hover and keyboard focus expose a themed scrollbar without changing width.
        page.mouse.move(1200,50);page.locator('#composer-input').focus();page.wait_for_timeout(220)
        assert region.evaluate('(el)=>getComputedStyle(el).scrollbarColor')=='rgba(0, 0, 0, 0) rgba(0, 0, 0, 0)'
        initial_width=region.evaluate('(el)=>el.clientWidth')
        region.hover();page.wait_for_timeout(220)
        assert region.evaluate('(el)=>getComputedStyle(el).scrollbarColor')!='rgba(0, 0, 0, 0) rgba(0, 0, 0, 0)'
        assert region.evaluate('(el)=>el.clientWidth')==initial_width
        page.wait_for_timeout(350)
        # Reading older text must not jump when another fragment arrives.
        region.evaluate('(el)=>{el.scrollTop=80;el.dispatchEvent(new Event("scroll"));}')
        before=region.evaluate('(el)=>el.scrollTop')
        transcript_before=page.locator('#chat').evaluate('(el)=>el.scrollTop')
        delta='Another streamed line below your reading position.\n'
        emit('thinking_delta',turn_id='turn-thinking',operation_id='scroll-thinking',offset=len(thought),text=delta);thought+=delta
        page.wait_for_timeout(80)
        assert region.evaluate('(el)=>el.scrollTop')==before
        assert page.locator('#chat').evaluate('(el)=>el.scrollTop')==transcript_before, (transcript_before,page.locator('#chat').evaluate('(el)=>el.scrollTop'))
        expect(row.locator('.thinking-latest')).to_be_visible()
        # Position the transcript explicitly for evidence (the assertion above
        # verifies it never moved automatically while reading older reasoning).
        page.locator('#chat').evaluate('(el)=>el.scrollTop=el.scrollHeight')
        page.mouse.move(1200,50)
        screenshot('08-thinking-reading-older-dark.png')
        row.locator('.thinking-latest').click();page.wait_for_timeout(50)
        assert region.evaluate('(el)=>el.scrollHeight-el.scrollTop-el.clientHeight<3')
        expect(row.locator('.thinking-latest')).to_be_hidden()
        row.locator('.thinking-more').click();page.wait_for_timeout(50)
        assert region.evaluate('(el)=>el.clientHeight>200 && el.clientHeight<=420 && el.scrollHeight>el.clientHeight')
        region.focus();page.keyboard.press('Home');page.wait_for_timeout(150)
        expect(region).to_have_attribute('data-follow','false')
        row.locator('.thinking-latest').click();page.wait_for_timeout(50)
        if page.locator('#jump-latest').is_visible(): page.locator('#jump-latest').click()
        screenshot('09-thinking-following-dark.png')
        # Existing selection and DOM nodes survive streaming additions.
        region.evaluate('''(el)=>{const r=document.createRange();r.selectNodeContents(el.firstChild);
            const s=getSelection();s.removeAllRanges();s.addRange(r);}''')
        selected=page.evaluate('getSelection().toString()')
        delta='The final streamed fixture sentence.\n'
        emit('thinking_delta',turn_id='turn-thinking',operation_id='scroll-thinking',offset=len(thought),text=delta);thought+=delta
        page.wait_for_timeout(50)
        assert page.evaluate('getSelection().toString()')==selected
        expect(region).to_have_attribute('data-follow','false')
        page.evaluate('getSelection().removeAllRanges()')
        page.emulate_media(reduced_motion='reduce')
        delta='Reduced-motion fixture sentence.\n'
        emit('thinking_delta',turn_id='turn-thinking',operation_id='scroll-thinking',offset=len(thought),text=delta)
        assert region.locator('.thinking-chunk').last.evaluate('(el)=>getComputedStyle(el).animationName')=='none'
        page.locator('#btn-theme').click();expect(page.locator('html')).to_have_attribute('data-theme','light')
        screenshot('10-thinking-light.png')
        page.locator('#btn-theme').click()
        for width in (320,390,760,1024,1440):
            page.set_viewport_size({'width':width,'height':960})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            expect(page.locator('#btn-theme')).to_be_in_viewport()
            expect(page.locator('#composer-input')).to_be_in_viewport()
        emit('thinking_done',turn_id='turn-thinking',operation_id='scroll-thinking',status='completed')
        # Inline question with suggested answers: the run keeps going, nothing pauses.
        emit('user',turn_id='turn-question',text='Controlled fixture: which board variant?')
        page.locator('#composer-input').fill('Keep this chat draft')
        placeholder=page.locator('#composer-input').get_attribute('placeholder')
        emit('question',turn_id='turn-question',question_id='q-fixture',question='Which Raspberry Pi variant is this case for?',
             options=[{'label':'Pi 3B','description':'Original 3B port layout'},{'label':'Pi 3B+','description':'PoE header, same outline'}],multi_select=False)
        card=page.locator('.question-card[data-state="open"]');expect(card).to_be_visible()
        expect(card.locator('.question-option')).to_have_count(3)
        expect(page.locator('#composer-input')).to_have_value('Keep this chat draft')
        assert page.locator('#composer-input').get_attribute('placeholder')==placeholder
        screenshot('08-question-open.png')
        card.locator('.question-option').nth(1).click()
        page.wait_for_timeout(60)
        assert commands[-1]=={'t':'answer','question_id':'q-fixture','selected':['Pi 3B+'],'text':''},commands[-1]
        emit('answer',turn_id='turn-question',question_id='q-fixture',selected=['Pi 3B+'],text='',summary='Pi 3B+')
        expect(page.locator('.question-card[data-state="completed"] .question-answer')).to_have_text('You answered: Pi 3B+')
        expect(page.locator('.question-card .question-option').nth(1)).to_have_attribute('aria-checked','true')
        expect(page.locator('.question-card .question-option').first).to_be_disabled()
        screenshot('09-question-answered.png')
        # Multi-select supports both choices and a local written answer.
        emit('question',turn_id='turn-question',question_id='q-multi',question='Which ports need openings?',
             options=[{'label':'USB','description':''},{'label':'HDMI','description':''},{'label':'Ethernet','description':''}],multi_select=True)
        multi=page.locator('.question-card[data-state="open"]')
        multi.locator('.question-option').nth(0).click();multi.locator('.question-option').nth(2).click()
        expect(multi.locator('.question-submit')).to_be_enabled()
        expect(multi.locator('.question-option')).to_have_count(4)
        multi.locator('.question-other').click();multi.locator('.question-response').fill('plus the audio jack')
        multi.locator('.question-submit').click();page.wait_for_timeout(60)
        assert commands[-1]=={'t':'answer','question_id':'q-multi','selected':['USB','Ethernet'],'text':'plus the audio jack'},commands[-1]
        expect(page.locator('#composer-input')).to_have_value('Keep this chat draft')
        emit('answer',turn_id='turn-question',question_id='q-multi',selected=['USB','Ethernet'],text='plus the audio jack',summary='USB, Ethernet; plus the audio jack')
        expect(page.locator('.question-card[data-state="completed"]')).to_have_count(2)
        for qid,options in [('q-custom',[{'label':'Small'},{'label':'Large'}]),('q-short',[])]:
            emit('question',turn_id='turn-question',question_id=qid,question='What size?',options=options,multi_select=False)
            local=page.locator('.question-card[data-state="open"]')
            expect(local.locator('.question-option')).to_have_count(len(options)+1)
            local.locator('.question-other').click()
            expect(local.locator('.question-submit')).to_be_disabled()
            local.locator('.question-response').fill('42 mm')
            emit('note',message='Unrelated update must preserve the answer draft')
            expect(local.locator('.question-response')).to_have_value('42 mm')
            local.locator('.question-response').press('Enter');page.wait_for_timeout(60)
            assert commands[-1]=={'t':'answer','question_id':qid,'selected':[],'text':'42 mm'},commands[-1]
            expect(page.locator('#composer-input')).to_have_value('Keep this chat draft')
            emit('answer',turn_id='turn-question',question_id=qid,selected=[],text='42 mm',summary='42 mm')
        emit('done',turn_id='turn-question',reason='Fixture complete')
        emit('done',turn_id='turn-thinking',reason='Fixture complete');active=False
        page.reload(wait_until='networkidle');page.locator('.session-tab').first.click()
        replay=page.locator('[data-operation-id="scroll-thinking"] .thinking-preview')
        assert replay.inner_text()==thought+delta
        assert replay.locator('.thinking-chunk').first.evaluate('(el)=>getComputedStyle(el).animationName')=='none'
        expect(page.locator('[data-operation-id="native-stream"]')).to_have_attribute('data-status','completed')
        expect(page.locator('[data-operation-id="cancel-draft"]')).to_have_attribute('data-status','cancelled')
        expect(page.locator('html')).to_have_attribute('data-theme','dark')
        assert not errors,errors
        browser.close()
    report={'status':'passed','browser_errors':errors,'events':len(events),'commands':commands,'evidence':'controlled streaming fixture, not live research','screenshots':[p.name for p in artifacts.glob('*.png')]}
    # Evidence output, not application source.
    (artifacts/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'artifacts':str(artifacts),**report},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='https://chat-fixture.test/');run(parser.parse_args().base)
