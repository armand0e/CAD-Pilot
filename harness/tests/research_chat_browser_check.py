"""Research calls retain their place in chat across progress, steering and replay."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

WEB=Path(__file__).resolve().parents[1]/'web'


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':900,'height':800})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def serve(route):
            path=urlsplit(route.request.url).path
            if path=='/':
                route.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/static/theme.css"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/chat/chat.css"><div id="chat" style="width:500px;height:600px;overflow:auto"></div><button id="jump"></button><div id="announce"></div>')
            else:route.fulfill(path=str(WEB/path.removeprefix('/static/')))
        page.route('**/*',serve)
        page.goto('https://research-chat-fixture.test/')
        page.evaluate('''async()=>{
          const {ChatPanel}=await import('/static/chat/panel.js');
          window.panel=new ChatPanel(document.querySelector('#chat'),document.querySelector('#jump'),document.querySelector('#announce'));
          window.events=[];window.emit=event=>{event={id:events.length+1,event_id:'event-'+events.length,ts:100+events.length,turn_id:'first',...event};events.push(event);panel.consume(event);};
        }''')
        def emit(kind,**fields):page.evaluate('event=>emit(event)',{'t':kind,**fields})
        def invoke(call,task,turn='first'):
            emit('tool_input_start',operation_id=call,turn_id=turn)
            emit('tool_input_done',operation_id=call,tool='research_dimensions',arguments={'part_identity':task},turn_id=turn)
            emit('research_start',operation_id=call,operation='research_dimensions',query=task,turn_id=turn)
        emit('user',text='Make an enclosure for this board.')
        emit('assistant',message='I will check the board dimensions first.')
        invoke('call-one','Fixture board')
        card=page.locator('.research-agent[data-operation-id="call-one"]')
        expect(card).to_have_count(1)
        expect(page.locator('.timeline-entry[data-operation-id="call-one"]')).to_have_count(0)
        expect(card).to_have_attribute('open','')
        source={'id':'drawing','title':'Mechanical drawing','url':'https://example.com/drawing.pdf','kind':'search_result'}
        progress={'agent_id':'child-one','task':'Fixture board','status':'running','started_at':100,'dimensions':['Board outline','Hole spacing'],'searches':1,'sources':[source]}
        emit('research_agent',**progress,activity='Searching for documentation',detail='Fixture board mechanical dimensions')
        expect(card.locator('.research-agent-goals')).to_contain_text('Hole spacing')
        expect(card.locator('.research-agent-counts')).to_contain_text('1 source found')
        emit('research_agent',**progress,activity='Reading the mechanical drawing',detail='https://example.com/drawing.pdf')
        expect(card.locator('.research-agent-step')).to_have_count(3)
        emit('research_agent',**progress,activity='Reading the mechanical drawing',detail='https://example.com/drawing.pdf')
        expect(card.locator('.research-agent-step')).to_have_count(3)
        # Tabs work by keyboard; incoming progress must not reset selection or source focus.
        card.get_by_role('tab',name='Activity',exact=True).focus()
        page.keyboard.press('ArrowRight')
        expect(card.get_by_role('tab',name='Sources (1)',exact=True)).to_have_attribute('aria-selected','true')
        card.get_by_role('link',name='Mechanical drawing').focus()
        page.evaluate('window.sourceLink=document.activeElement')
        progress['sources'][0]['kind']='pdf'
        emit('research_agent',**progress,activity='Reviewing the sources',pages_read=1)
        assert page.evaluate('sourceLink===document.activeElement')
        expect(card.locator('.research-agent-source small')).to_have_text('Read')
        expect(card.get_by_role('tab',name='Sources (1)',exact=True)).to_have_attribute('aria-selected','true')
        card.get_by_role('tab',name='Activity',exact=True).click()
        page.evaluate('window.cardOne=document.querySelector(".research-agent")')
        emit('user',turn_id='steering',text='Use the newer board revision.')
        # Old workers can stamp subsequent child events with the new parent turn.
        emit('research_agent',turn_id='steering',agent_id='child-one',task='Fixture board',status='running',activity='Checking the revision',started_at=100,sources=[])
        expect(page.locator('[data-turn-id="first"] .research-agent')).to_have_count(1)
        expect(page.locator('[data-turn-id="steering"] .research-agent')).to_have_count(0)
        expect(card.locator('.research-agent-badge')).to_have_text('Running')
        emit('research_agent',turn_id='steering',agent_id='child-one',task='Fixture board',status='completed',activity='Research complete',started_at=100,finished_at=130,sources=[source],summary='Board dimensions documented.',documented_dimensions=2)
        expect(card.get_by_role('tab',name='Activity',exact=True)).to_have_attribute('aria-selected','true')
        emit('research_result',operation_id='call-one',operation='research_dimensions',summary='Board dimensions documented.',sources=[])
        emit('tool_settled',operation_id='call-one',status='completed')
        emit('assistant',turn_id='steering',message='I have the board dimensions; next I will check the connector.')
        invoke('call-two','Connector','steering')
        emit('research_agent',turn_id='steering',agent_id='child-two',task='Connector',status='running',activity='Looking for the connector drawing',started_at=140,sources=[])
        expect(page.locator('.research-agent')).to_have_count(2)
        expect(page.locator('.timeline-entry[data-operation-id="call-two"]')).to_have_count(0)
        assert page.evaluate('cardOne===document.querySelector(".research-agent") && cardOne.open')
        before=page.evaluate('Array.from(document.querySelectorAll(".turn-content > *")).map(el=>el.dataset.operationId || el.textContent)')
        step_count=card.locator('.research-agent-step').count()
        page.evaluate('panel.replay(events,true)')
        assert before==page.evaluate('Array.from(document.querySelectorAll(".turn-content > *")).map(el=>el.dataset.operationId || el.textContent)')
        assert page.evaluate('cardOne===document.querySelector(".research-agent") && cardOne.open')
        expect(card.locator('.research-agent-step')).to_have_count(step_count)
        # Terminal cancellation must not be changed to success by a late result.
        emit('research_agent',turn_id='steering',agent_id='child-two',task='Connector',status='cancelled',activity='Research stopped',started_at=140,finished_at=160)
        emit('research_result',turn_id='steering',operation_id='call-two',operation='research_dimensions',sources=[])
        expect(page.locator('[data-operation-id="call-two"] .research-agent-badge')).to_have_text('Stopped')
        # A fresh page recreates the same placement from the durable transcript.
        page.evaluate('panel.reset();document.querySelector("#chat").replaceChildren();panel.replay(events,false)')
        expect(page.locator('[data-turn-id="first"] .research-agent')).to_have_count(1)
        expect(page.locator('[data-turn-id="steering"] .research-agent')).to_have_count(1)
        expect(page.locator('[data-operation-id="call-one"] .research-agent-badge')).to_have_text('Complete')
        expect(page.locator('[data-operation-id="call-two"] .research-agent-badge')).to_have_text('Stopped')
        expect(card.locator('.research-agent-step')).to_have_count(step_count)
        card.locator('summary').click()
        expect(card.get_by_role('tab',name='Findings',exact=True)).to_have_attribute('aria-selected','true')
        expect(card.locator('.research-agent-documented')).to_have_text('2 dimensions documented')
        expect(card.locator('.research-agent-outcome')).to_have_text('Board dimensions documented.')
        # An untouched active card shows findings on completion, including unresolved dimensions.
        emit('user',turn_id='third',text='Check the connector clearance too.')
        invoke('call-three','Connector clearance','third')
        emit('research_agent',turn_id='third',agent_id='child-three',task='Connector clearance',status='incomplete',activity='Research finished with unresolved questions',started_at=170,finished_at=190,documented_dimensions=0,unknowns=['Connector overhang is not documented.'])
        third=page.locator('[data-operation-id="call-three"]')
        expect(third.get_by_role('tab',name='Findings',exact=True)).to_have_attribute('aria-selected','true')
        expect(third.locator('.research-agent-unknowns')).to_have_text('Connector overhang is not documented.')
        expect(third.locator('.research-agent-badge')).to_have_text('Needs follow-up')
        page.screenshot(path='/tmp/cad-research-workspace-desktop.png')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#chat').evaluate('(el)=>{el.style.width="100%";}')
        third.scroll_into_view_if_needed()
        assert third.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path='/tmp/cad-research-workspace-mobile.png')
        # Reopening after the worker exits freezes the elapsed clock and live marker.
        emit('user',turn_id='fourth',text='Research the mounting screws.')
        invoke('call-four','Mounting screws','fourth')
        emit('research_agent',turn_id='fourth',agent_id='child-four',task='Mounting screws',status='running',activity='Searching for documentation',started_at=200,ts=210)
        page.evaluate('panel.reset();document.querySelector("#chat").replaceChildren();panel.replay(events,false)')
        fourth=page.locator('[data-operation-id="call-four"]')
        expect(fourth.locator('.research-agent-badge')).to_have_text('Interrupted')
        expect(fourth.locator('.research-agent-elapsed')).to_have_text('0:10')
        page.evaluate('()=>{const original=Date.now;try{Date.now=()=>original()+61000;for(const view of panel.views.values())view.tick();}finally{Date.now=original;}}')
        expect(fourth.locator('.research-agent-elapsed')).to_have_text('0:10')
        assert not errors,errors
        browser.close()
        print('PASS: inline invocation cards, live activity trail, keyboard tabs, stable source focus, findings, multiple calls, steering ownership, reconnect/reload history and mobile layout')


if __name__=='__main__':main()
