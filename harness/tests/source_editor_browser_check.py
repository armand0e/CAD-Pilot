"""Shipped source editor with controlled API routes; no live project mutations."""
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from PIL import Image
from playwright.sync_api import sync_playwright, expect

WEB=Path(__file__).resolve().parents[1]/'web'


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':960})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        session={'id':'fixture','project_id':'0123456789abcdef','app':{'id':'freecad','name':'FreeCAD'},'engine':'hybrid','app_alive':True,'width':1280,'height':900}
        project={'id':session['project_id'],'name':'Fixture','head':None,'revisions':[],'design':None,'geometry':None,'research':None}
        files={'model.py':'parts = {}\n','design-spec.json':'{"version":1,"objective":"Curved case"}\n','CAD_GUIDE.md':'Fixture guide'}
        versions={name:'v1' for name in files};sockets=[];builds=[]
        def serve(route):
            url=urlsplit(route.request.url);path=url.path;method=route.request.method
            if path=='/api/status':data={'sessions':[session],'missing_tools':[],'policy':{'ok':True},'planner':{'ok':True},'research':{'enabled':True}}
            elif path=='/api/apps':data={'apps':[session['app']]}
            elif path=='/api/projects':data={'projects':[]}
            elif path=='/api/auth/session':data={'enabled':False}
            elif path=='/api/settings':data={'active_model':'fixture','models':[{'name':'fixture','model':'fixture','base_url':'https://fixture.test/v1'}],'efforts':['off','low']}
            elif path.endswith('/workspace/file'):
                if method=='PUT':
                    body=json.loads(route.request.post_data);name=body['path']
                    if body['sha256']!=versions[name]:route.fulfill(status=409,json={'detail':'File changed; reload before saving'});return
                    files[name]=body['content'];versions[name]='v2'
                else:name=parse_qs(url.query)['path'][0]
                data={'path':name,'content':files[name],'sha256':versions[name]}
            elif path.endswith('/workspace'):data={'files':[{'path':name,'bytes':len(content),'sha256':versions[name]} for name,content in files.items()]}
            elif path.endswith('/project'):
                builds.append(json.loads(route.request.post_data));data=project
            elif path==f'/api/projects/{project["id"]}':data=project
            else:
                file=WEB/('index.html' if path=='/' else path.removeprefix('/static/'))
                route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
                return
            route.fulfill(json=data)
        page.route('**/*',serve)
        hold_reconnect=[False]
        def socket(ws):
            sockets.append(ws)
            if not hold_reconnect[0]:ws.send(json.dumps({'t':'snapshot','active':False,'paused':False,'events':[],'transcript':[],'phase':'idle'}))
        page.route_web_socket('**/ws/agent/*',socket)
        frame=io.BytesIO();Image.new('RGB',(1280,900),'#333333').save(frame,'JPEG')
        page.route_web_socket('**/ws/view/*',lambda ws:ws.send(frame.getvalue()))
        page.goto('https://workspace-fixture.test/');page.locator('.session-tab').first.click()
        page.locator('#model-panel>summary').click();page.locator('#workspace-editor>summary').click()
        expect(page.locator('#workspace-content')).to_have_value(re.compile('Curved case'))
        page.locator('#composer-input').fill('Keep this chat draft')
        page.locator('#workspace-file').select_option('model.py')
        page.locator('#workspace-content').fill('parts = {"Case": shape}\n')
        expect(page.locator('#workspace-build')).to_be_disabled()
        page.locator('#workspace-file').select_option('design-spec.json')
        expect(page.locator('#workspace-build')).to_be_disabled()
        page.locator('#workspace-file').select_option('model.py')
        expect(page.locator('#workspace-content')).to_have_value('parts = {"Case": shape}\n')
        page.locator('#workspace-save').click()
        expect(page.locator('#workspace-save')).to_be_disabled()
        expect(page.locator('#workspace-build')).to_be_enabled()
        page.locator('#workspace-build').click();page.wait_for_timeout(80)
        assert builds[-1]=={'operation':'source_build','entrypoint':'model.py','expected_head':None},builds
        expect(page.locator('#composer-input')).to_have_value('Keep this chat draft')
        sockets[-1].send(json.dumps({'t':'control','locked':True,'mode':'auto'}))
        expect(page.locator('#workspace-content')).not_to_be_editable()
        expect(page.locator('#workspace-build')).to_be_disabled()
        research={'t':'research_agent','agent_id':'research-1','task':'Raspberry Pi 5 · mounting dimensions',
                  'status':'running','activity':'Reading a source','detail':'Official mechanical drawing',
                  'started_at':time.time()-62,'ts':time.time(),'searches':2,'pages_read':1,
                  'dimensions':['Board outline','Mounting-hole positions'],
                  'sources':[{'id':'official','title':'Raspberry Pi mechanical drawing','url':'https://www.raspberrypi.com/documentation/','kind':'pdf'},
                             {'id':'unsafe','title':'Unsafe link','url':'javascript:alert(1)','kind':'page'}]}
        sockets[-1].send(json.dumps(research))
        card=page.locator('.research-agent')
        expect(card).to_be_visible();expect(card.locator('.research-agent-badge')).to_have_text('Running')
        expect(card.locator('.research-agent-elapsed')).to_have_text(re.compile('1:[0-5][0-9]'))
        card.locator('summary').click()
        expect(card.locator('a')).to_have_count(1)
        expect(card.locator('.research-agent-counts')).to_contain_text('1 page read')
        expect(page.locator('#composer-input')).to_have_value('Keep this chat draft')
        page.screenshot(path='/tmp/cad-research-sidebar.png')
        hold_reconnect[0]=True
        previous=len(sockets);sockets[-1].close(code=1001,reason='Fixture connection drop')
        expect(card.locator('.research-agent-badge')).to_have_text('Reconnecting')
        expect(card).not_to_have_class(re.compile('is-live'))
        for _ in range(30):
            if len(sockets)>previous:break
            page.wait_for_timeout(100)
        assert len(sockets)>previous,'Agent stream did not reconnect'
        # Reconnect uses the snapshot even when chat's bounded event tail is empty.
        sockets[-1].send(json.dumps({'t':'snapshot','active':True,'paused':False,'mode':'auto','events':[],
                                    'transcript':[],'phase':'modeling','research_agents':[research]}))
        expect(page.locator('.research-agent')).to_have_count(1)
        expect(page.locator('.research-agent-badge')).to_have_text('Running')
        for status,label in [('completed','Complete'),('cancelled','Stopped'),('failed','Failed')]:
            sockets[-1].send(json.dumps({**research,'status':status,'finished_at':time.time(),'ts':time.time(),
                                        'activity':'Research complete' if status=='completed' else 'Research stopped',
                                        'summary':'Board outline documented; mounting tolerance remains unknown.'}))
            expect(page.locator('.research-agent-badge')).to_have_text(label)
            expect(page.locator('.research-agent')).not_to_have_class(re.compile('is-live'))
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('.research-agent')).to_be_visible()
        assert page.locator('.research-agent').bounding_box()['width']<=390
        assert not errors,errors
        browser.close()
        print('PASS: source editor, independent chat draft, research sidebar progress, sources, reconnect, completion/cancellation/failure and mobile layout')


if __name__=='__main__':main()
