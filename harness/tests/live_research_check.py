"""Opt-in: stock model reads a real manufacturer spec, builds, and is independently graded.

No expected mounting pitch is supplied to the model. User dimensions and source URL
are explicit; separate live search probe covers engine fallback without API keys.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'harness'))
sys.path.insert(0,str(ROOT/'training'))
from server.research import ResearchTool
from cad_policy.cad_sandbox import sandbox_command


def main():
    output=Path(tempfile.mkdtemp(prefix='web-research-',dir=ROOT/'runs/harness-checks'))
    print(output,flush=True)
    search=asyncio.run(ResearchTool({'enabled':True}).search('Arduino UNO R3 dimensions'))
    assert search['sources'] and any('arduino' in source['url'] for source in search['sources']),search
    (output/'search.json').write_text(json.dumps(search,indent=2))
    print('Local-browser search:',search['provider'],flush=True)
    base='http://127.0.0.1:7802'
    prompt=("Look up the mounting-hole spacing of the Noctua NF-A14 PWM fan using the manufacturer page "
            "https://www.noctua.at/en/products/nf-a14-pwm as the primary source. Then create a 150 × 150 × 3 mm adapter plate, "
            "minimum corner at (0,0,0), with four 5 mm diameter through holes on that fan's mounting square centered "
            "on the plate, and a centered 120 mm diameter through cutout for airflow. Do not guess the fan's mounting spacing. "
            "Cite the source and keep the spacing editable.")
    sid=None
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def remember_session(response):
                nonlocal sid
                if response.url == base+'/api/sessions' and response.request.method=='POST' and response.ok:
                    sid=response.json()['id']
            page.on('response',remember_session)
            page.goto(base,wait_until='networkidle')
            page.locator('.app-tile',has_text='FreeCAD').click()
            expect(page.locator('#connection-status')).to_have_text('Live',timeout=30000)
            sid=page.evaluate("localStorage.getItem('cadpilot-session')")
            page.fill('#composer-input',prompt);page.locator('#btn-send').click()
            deadline=time.monotonic()+300;last=0
            while time.monotonic()<deadline:
                snapshot=page.request.get(f'{base}/api/sessions/{sid}/activity').json()
                for event in snapshot['events']:
                    if event['id']>last and event['t'] in ('research_start','research_error','tool_error','done','pause'):
                        print(json.dumps(event),flush=True)
                    last=max(last,event['id'])
                if any(e['t']=='user' and e.get('text')==prompt for e in snapshot['events']) and (not snapshot['active'] or snapshot['paused']):break
                page.wait_for_timeout(500)
            (output/'activity.json').write_text(json.dumps(snapshot,indent=2))
            assert not snapshot['active'],snapshot['events'][-8:]
            artifacts=[e for e in snapshot['events'] if e['t']=='artifact']
            assert len(artifacts)==1,snapshot['events'][-8:]
            project=artifacts[0]['project'];research=project.get('research')
            assert research and any('124.5' in fact['statement'] for fact in research['notes']['facts']),research
            assert any(source['kind']=='page' and 'noctua.at' in source['url'] for source in research['sources'])
            work=output/'grade';work.mkdir()
            response=page.request.get(f"{base}/api/projects/{project['id']}/{project['head']}/model.FCStd")
            assert response.ok
            (work/'model.FCStd').write_bytes(response.body());(work/'task.json').write_text(json.dumps({'kind':'fan'}))
            command=sandbox_command('freecad',work,extra_binds=[(ROOT/'harness/tests/reference_shapes.py','/oracle.py')])+['/opt/cad/usr/bin/python','/oracle.py']
            grade=subprocess.run(command,capture_output=True,text=True,timeout=90)
            assert grade.returncode==0,grade.stdout+grade.stderr
            page.locator('#model-panel').evaluate('(e)=>e.open=true')
            page.locator('#model-research').evaluate('(e)=>e.open=true')
            page.screenshot(path=str(output/'workspace.png'))
            page.reload(wait_until='networkidle')
            expect(page.locator('#model-name')).to_contain_text('r0001',timeout=30000)
            expect(page.locator('#model-research-content')).to_contain_text('124.5')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/'mobile.png'))
            assert not errors,errors
            (output/'result.json').write_text(json.dumps({'success':True,'grade':json.loads((work/'reference.json').read_text()),
                                                        'browser_errors':errors,'project_id':project['id']},indent=2))
            print((output/'result.json').read_text(),flush=True)
            browser.close()
    finally:
        if sid:httpx.delete(f'{base}/api/sessions/{sid}',timeout=30).raise_for_status()


if __name__=='__main__':main()
