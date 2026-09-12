"""Stock-model spacer and cross-axis bracket, independently reference-graded."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'training'))
from cad_policy.cad_sandbox import sandbox_command

output=Path(tempfile.mkdtemp(prefix='native-variety-',dir=ROOT/'runs/harness-checks'))
print(str(output),flush=True)
base='http://127.0.0.1:7802'
tasks={
 'spacer':'Create a hollow cylindrical spacer, 20 mm outer diameter, 10 mm inner diameter, and 15 mm tall. Center its axis at X=Y=0, with its bottom at Z=0.',
 'bracket':'Create an L bracket by joining a 60 × 30 × 5 mm floor and a 5 × 30 × 40 mm upright. Both boxes start at the origin and extend in positive X/Y/Z, so they overlap at the heel. Add a 6 mm through hole along Z centered at X=45,Y=15 in the floor, and a 6 mm through hole along X centered at Y=15,Z=25 in the upright. Keep it one connected solid.'}
with sync_playwright() as p:
 browser=p.chromium.launch()
 for kind,prompt in tasks.items():
  page=browser.new_page(viewport={'width':1440,'height':1000},bypass_csp=True);sid=None
  try:
   page.goto(base,wait_until='networkidle');page.locator('.app-tile',has_text='FreeCAD').click()
   page.wait_for_function("document.querySelector('#connection-status').textContent === 'Live'")
   sid=page.evaluate("localStorage.getItem('cadpilot-session')")
   page.fill('#composer-input',prompt);page.locator('#btn-send').click()
   deadline=time.monotonic()+180
   while time.monotonic()<deadline:
    snapshot=page.request.get(f'{base}/api/sessions/{sid}/activity').json()
    if snapshot['events'] and (not snapshot['active'] or snapshot['paused']):break
    page.wait_for_timeout(500)
   (output/f'{kind}-activity.json').write_text(json.dumps(snapshot,indent=2))
   assert not snapshot['active'],snapshot['events'][-8:]
   artifacts=[e for e in snapshot['events'] if e['t']=='artifact'];assert len(artifacts)==1,snapshot['events'][-8:]
   project=artifacts[0]['project'];work=output/kind;work.mkdir()
   response=page.request.get(f"{base}/api/projects/{project['id']}/{project['head']}/model.FCStd");assert response.ok
   (work/'model.FCStd').write_bytes(response.body());(work/'task.json').write_text(json.dumps({'kind':kind}))
   command=sandbox_command('freecad',work,extra_binds=[(ROOT/'harness/tests/reference_shapes.py','/oracle.py')])+['/opt/cad/usr/bin/python','/oracle.py']
   graded=subprocess.run(command,capture_output=True,text=True,timeout=90)
   assert graded.returncode==0,graded.stdout+graded.stderr
   print(kind,(work/'reference.json').read_text(),flush=True)
   page.screenshot(path=str(output/f'{kind}.png'))
  finally:
   sid=sid or page.evaluate("localStorage.getItem('cadpilot-session')")
   if sid:page.request.delete(f'{base}/api/sessions/{sid}')
   page.close()
 browser.close()
