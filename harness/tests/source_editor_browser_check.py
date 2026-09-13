"""Shipped source editor with controlled API routes; no live project mutations."""
import io
import json
import re
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
        def socket(ws):
            sockets.append(ws);ws.send(json.dumps({'t':'snapshot','active':False,'paused':False,'events':[],'transcript':[],'phase':'idle'}))
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
        assert not errors,errors
        browser.close()
        print('PASS: source/spec editor, independent chat draft, file save/build, unsaved drafts and active-agent protection')


if __name__=='__main__':main()
