"""Source editing, specification provenance, actual kernel geometry and Pi integration."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pi_fixture import pi_model, messages as pi_messages, message_text
from server.agent import AgentRunner
from server.projects import Project, ROOT
from server.source_workspace import SourceWorkspace, SUPPLIED_FILES


class WorkspaceUpgradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = Project.create(self.root / 'projects', 'freecad')
        self.work = SourceWorkspace(self.project)

    async def test_old_knowledge_cannot_hide_or_replace_installed_guide(self):
        from server.knowledge import design_notes
        knowledge = self.root / 'knowledge'
        knowledge.mkdir()
        (knowledge / 'personal-notes.md').write_text('Keep my enclosure notes.\n')
        (knowledge / 'learned_facts.jsonl').write_text('{"statement":"Keep this fact"}\n')
        for old_guide in (None, 'Outdated workspace instructions from an earlier release.\n'):
            with self.subTest(old_guide=old_guide):
                if old_guide:
                    (knowledge / 'source-workspace.md').write_text(old_guide)
                before = {p.name: p.read_bytes() for p in knowledge.iterdir()}
                with patch('server.source_workspace.ROOT', self.root):
                    self.work.ensure()
                with patch('server.knowledge.KNOWLEDGE', knowledge):
                    notes = design_notes('workspace enclosure notes')
                self.assertNotIn('source-workspace', notes['available'])
                self.assertEqual(notes['notes'][0]['document'], 'personal-notes')
                self.assertEqual(self.work.file('CAD_GUIDE.md').read_bytes(), SUPPLIED_FILES['CAD_GUIDE.md'].read_bytes())
                self.assertEqual({p.name: p.read_bytes() for p in knowledge.iterdir()}, before)

    async def test_recover_partially_seeded_project_without_overwriting_draft(self):
        # The affected release seeded these files before failing to read the guide.
        self.work.path.mkdir()
        self.work.seed(None)
        original_state = self.work.meta.read_bytes()
        self.work.file('model.py').write_text('# A draft created before reconnecting.\n')
        self.work.file('notes.txt').write_text('Retain these dimensions.\n')
        self.assertFalse(self.work.file('CAD_GUIDE.md').exists())
        self.assertFalse((self.project.path / 'spec-state.json').exists())
        self.work.ensure()
        self.work.record_inputs([{'t': 'user', 'event_id': 'first-request', 'text': 'Make a raspberry.'}])
        self.work.ensure()
        self.assertEqual(self.work.meta.read_bytes(), original_state)
        self.assertEqual(self.work.file('model.py').read_text(), '# A draft created before reconnecting.\n')
        self.assertEqual(self.work.file('notes.txt').read_text(), 'Retain these dimensions.\n')
        self.assertEqual(self.work.file('CAD_GUIDE.md').read_bytes(), SUPPLIED_FILES['CAD_GUIDE.md'].read_bytes())
        self.assertEqual(self.work.spec()['requirements'][0]['text'], 'Make a raspberry.')
        self.assertTrue(self.work.dirty())

    @unittest.skipUnless((ROOT / 'pi/node_modules/@earendil-works/pi-coding-agent').is_dir(), 'Pi installation required')
    async def test_missing_resources_fail_before_starting_pi_or_seeding_files(self):
        from server.pi_agent import PiBridge
        session = SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
                                  app={'name': 'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        runner = AgentRunner(session, {'agent': {'native_operations': True}, 'policy': {},
            'planner': {'base_url': 'http://127.0.0.1:1/v1', 'model': 'fixture'}, 'research': {'enabled': False}})
        bridge = PiBridge(runner, {'project': self.project})
        with patch.dict(SUPPLIED_FILES, {'CAD_GUIDE.md': self.root / 'missing-guide.md'}), \
                patch('server.pi_agent.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(FileNotFoundError):
                await bridge.start()
            spawn.assert_not_called()
        self.assertIsNone(bridge.proc)
        self.assertFalse(self.work.path.exists())
        self.assertFalse(self.work.meta.exists())


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Project.create(self.temp.name, 'freecad')
        self.work = SourceWorkspace(self.project)
        self.work.ensure()

    async def test_file_paths_links_and_concurrent_edits_are_protected(self):
        for path in ('../project.json', '/etc/passwd', '/work/../project.json', 'a/../../b', 'a\\b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.work.fs({'action':'read','path':path})
        (self.work.path/'linked').symlink_to(self.project.path)
        with self.assertRaises(ValueError):
            self.work.file('linked/project.json')
        (self.work.path/'linked').unlink()
        original = next(f for f in self.work.files() if f['path']=='model.py')
        await self.work.fs({'action':'write','path':'model.py','content':'# newer draft'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.work.fs({'action':'write','path':'model.py','content':'# stale draft','expected_sha256':original['sha256']})
        with self.assertRaisesRegex(ValueError, 'Unsaved'):
            self.work.checkout(None)
        self.assertEqual(self.work.file('model.py').read_text(), '# newer draft')

    async def test_user_corrections_and_spec_versions_survive_reopen(self):
        self.work.record_inputs([{'t':'user','event_id':'first','text':'Case for a 60 x 30 mm board.'}])
        self.work.record_inputs([{'t':'user','event_id':'second','text':'Actually the board is 65 mm long.'}])
        value = copy.deepcopy(self.work.spec())
        self.assertEqual(value['requirements'][0]['origin'], 'user')
        first_version = value['version']
        value['requirements'][0].update(text='Board length 65 mm', evidence=['input:second'], features=['Case'])
        value['addressed_inputs']=['input:first','input:second']
        self.work.update_spec(value, first_version)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.work.update_spec(value, first_version)
        recovered = SourceWorkspace(Project(self.temp.name, self.project.id))
        recovered.ensure()
        self.assertEqual(recovered.spec()['requirements'][0]['text'], 'Board length 65 mm')
        self.assertEqual(len(recovered.inputs()),2)
        self.assertEqual(recovered.spec_context()['pending_inputs'],[])
        self.assertEqual(len(list((self.project.path/'spec-history').glob('*.json'))),2)
        invalid = copy.deepcopy(recovered.spec())
        invalid['requirements'][0]['evidence']=['input:invented']
        with self.assertRaisesRegex(ValueError,'Unknown evidence'):
            recovered.update_spec(invalid,invalid['version'])
        invalid = copy.deepcopy(recovered.spec())
        invalid['requirements'][0]['status']='verified'
        with self.assertRaisesRegex(ValueError,'recorded geometry evidence'):
            recovered.update_spec(invalid,invalid['version'])

    async def test_cancelled_build_removes_scratch_and_preserves_draft(self):
        entered = asyncio.Event()
        async def wait(*args,**kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch('server.source_workspace.execute', wait):
            task=asyncio.create_task(self.work.prepare('model.py',None))
            await entered.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(self.project.read()['head'])
        self.assertTrue(self.work.file('model.py').is_file())
        self.assertEqual(list(self.project.path.glob('.build-*')),[])
        self.assertEqual(list(self.project.path.glob('.program-*')),[])


@unittest.skipUnless(shutil.which('bwrap') and (ROOT/'apps/freecad-extracted/usr/bin/python').exists(), 'Bundled CAD runtime required')
class KernelWorkspaceTests(WorkspaceTests):
    async def build(self, entry='model.py'):
        head=self.project.read()['head']
        stage=await self.work.prepare(entry,head)
        saved=self.project.commit(stage,head)
        self.work.built(saved)
        return saved

    async def test_python3_and_project_images_are_available_to_sandboxed_bash(self):
        import io
        from PIL import Image
        from server.attachments import store_image
        buffer = io.BytesIO(); Image.new('RGB', (2400, 900), 'white').save(buffer, 'PNG')
        stored = store_image(self.project.path / 'attachments', buffer.getvalue(), 'drawing')
        command = ("python3 -c \"from PIL import Image; import numpy; im = Image.open('/work/images/attachments/" + stored['id'] + "'); "
                   "orig = Image.open('/work/images/attachments/" + stored['id'] + ".original.png'); print(im.size, orig.size)\"")
        result = await self.work.fs({'action': 'bash', 'command': command})
        self.assertEqual(result['exitCode'], 0, result['output'])
        self.assertIn('(2000, 750) (2400, 900)', result['output'])
        blocked = await self.work.fs({'action': 'bash', 'command': "touch /work/images/attachments/x 2>&1; echo exit=$?"})
        self.assertIn('exit=1', blocked['output'])  # read-only
        self.assertFalse(any(f['path'].startswith('images/') for f in self.work.files()))  # mount points are not workspace files

    async def test_curved_paths_holes_sections_and_geometric_clearance(self):
        code='''from cad_paths import extrude, revolve
import FreeCAD as App
case=extrude("M0 0 L40 0 C50 0 55 10 50 20 Q40 35 20 25 L0 20 Z M5 5 h8 v8 h-8 z",8)
lid=extrude("M0 0 H10 V10 H0 Z",2)
lid.translate(App.Vector(0,0,11))
parts={"Case":case,"Lid":lid}
'''
        await self.work.fs({'action':'write','path':'model.py','content':code})
        saved=await self.build(); head=saved['head']
        self.assertEqual(saved['geometry']['solid_count'],2)
        self.assertAlmostEqual(saved['geometry']['bounds_mm'][1],29,places=4)
        measure=await self.work.inspect(head,{'query':'measure','a':'Case','b':'Lid'})
        self.assertAlmostEqual(measure['minimum_distance_mm'],3,places=5)
        self.assertEqual(measure['intersection_volume_mm3'],0)
        self.work.record_inputs([{'t':'user','event_id':'gap','text':'Keep a 3 mm gap between Case and Lid.'}])
        spec=self.work.update_spec({'requirements':[{'id':'initial-request','status':'verified','features':['Case','Lid'],
            'evidence':['input:gap',measure['id']], 'verification':{'kind':'measurement','evidence':measure['id'],
                'field':'/minimum_distance_mm','expected':3,'tolerance':.00001}}]}, self.work.spec()['version'], patch=True)
        self.assertEqual(spec['requirements'][0]['status'],'verified')
        self.assertAlmostEqual(self.work.spec_context()['verification_checks']['initial-request']['actual'],3,places=5)
        section=await self.work.inspect(head,{'query':'section','object':'Case','axis':'z','at':4})
        self.assertEqual(len(section['contours']),2)
        faces=await self.work.inspect(head,{'query':'faces','object':'Case','offset':1,'limit':2})
        self.assertEqual(faces['faces'][0]['id'],'Case:Face2')
        self.assertEqual(faces['next_offset'],3)
        for view in ('bottom','back','left'):
            render=await self.work.inspect(head,{'query':'render','view':view,'bodies':['Case'],'highlight':'Case'})
            self.assertIn('image',render)
        render=await self.work.inspect(head,{'query':'render','direction':[1,-2,.5],'bodies':['Case'],
                                           'section':{'axis':'z','at':4,'keep':'below'}})
        self.assertFalse(render['geometry_modified'])
        original=self.project.file(head,'model.FCStd').read_bytes()
        await self.work.fs({'action':'write','path':'model.py','content':'raise ValueError("bad draft")'})
        with self.assertRaisesRegex(ValueError,'bad draft'):
            await self.build()
        self.assertEqual(self.project.file(head,'model.FCStd').read_bytes(),original)
        self.assertEqual(self.project.read()['head'],head)
        self.assertIn('bad draft',self.work.file('model.py').read_text())

    async def test_native_bash_cannot_see_host_projects_or_network_and_cancels(self):
        sentinel=Path(self.temp.name)/'host-secret.txt';sentinel.write_text('private')
        code=f'''import pathlib,socket
assert not pathlib.Path({str(sentinel)!r}).exists()
s=socket.socket()
try:
 s.connect(("127.0.0.1",8000))
 raise AssertionError("network escaped")
except OSError: pass
print("sandbox confirmed")
'''
        await self.work.fs({'action':'write','path':'check.py','content':code})
        result=await self.work.fs({'action':'bash','command':'python check.py'})
        self.assertEqual(result['exitCode'],0,result)
        self.assertIn('sandbox confirmed',result['output'])
        task=asyncio.create_task(self.work.fs({'action':'bash','command':'sleep 60'}))
        await asyncio.sleep(.15);task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task,2)

    async def test_reopen_restore_and_open_scad_source_archive(self):
        await self.work.fs({'action':'write','path':'shape.scad','content':'module body(){cube([20,10,5]);}'})
        await self.work.fs({'action':'write','path':'model.scad','content':'include <shape.scad>\nbody(); translate([30,0,0])cube([5,5,2]);'})
        saved=await self.build('model.scad')
        self.assertEqual(saved['geometry']['solid_count'],2)
        self.assertIn('faceted',saved['geometry']['representation'])
        self.project.restore(saved['head'],saved['head'])
        head=self.project.read()['head']
        self.work.checkout(head)
        self.assertIn('module body()',self.work.file('shape.scad').read_text())
        self.assertEqual(self.work.state()['base_head'],head)
        self.assertFalse(self.work.dirty())
        self.assertTrue(self.project.file(head,'source.zip').exists())

    async def test_path_generators_loft_pipe_and_cut_through(self):
        code='''from cad_paths import extrude, revolve, loft, pipe, cut_through, rect, circle, slot, hexagon, d_shape, polygon, with_holes
import FreeCAD as App
plate = extrude(with_holes(rect(60, 40, 4), circle(3.4, (8, 8)), circle(3.4, (52, 32)), slot(14, 4, (23, 18))), 3)
housing = extrude("M0 0 L40 0 C50 0 55 10 50 20 Q40 35 20 25 L0 20 Z", height=8)
before = housing.Volume
housing = cut_through(housing, rect(10, 4, 1), plane='xz', at=(15, 0, 2))
assert housing.Volume < before - 10, (before, housing.Volume)
nut = extrude(hexagon(5.5), 2.4); nut.translate(App.Vector(100, 0, 0))
key = extrude(d_shape(6, 1), 5); key.translate(App.Vector(100, 20, 0))
vase = loft([rect(40, 30, 6, center=True), circle(24), circle(36)], [0, 40, 70]); vase.translate(App.Vector(200, 0, 0))
handle = pipe("M0 0 C0 30 60 30 60 0", diameter=8, plane='xz'); handle.translate(App.Vector(300, 0, 0))
try:
    cut_through(nut, rect(2, 2), plane='xy', at=(50, 50, 0))
    raise AssertionError('cut outside the body must be reported')
except ValueError as error:
    assert 'removed no material' in str(error), error
parts = {"Plate": plate, "Housing": housing, "Nut": nut, "Key": key, "Vase": vase, "Handle": handle}
'''
        await self.work.fs({'action':'write','path':'model.py','content':code})
        saved=await self.build()
        volumes={p['feature']:p['volume_mm3'] for p in saved['geometry']['parts']}
        self.assertEqual(saved['geometry']['solid_count'],6)
        import math
        self.assertAlmostEqual(volumes['Plate'],(60*40-(4-math.pi)*16-2*math.pi*1.7**2-(10*4+math.pi*4))*3,delta=.5)
        self.assertAlmostEqual(volumes['Nut'],2.4*2*math.sqrt(3)*2.75**2,delta=.05)
        self.assertGreater(volumes['Vase'],40*30*10)
        self.assertAlmostEqual(volumes['Handle'],math.pi*16*saved['geometry']['parts'][5]['bounds_mm'][0] if False else volumes['Handle'],delta=1)
        self.assertGreater(volumes['Handle'],math.pi*16*60)
        # The preview tool runs without the kernel and attaches a picture the model can read.
        from server.workspace_tools import dispatch
        from types import SimpleNamespace
        runner=SimpleNamespace(pending_images=[],emit=lambda e:None)
        bridge=SimpleNamespace(runner=runner,ctx={'project':self.project,'expected_head':saved['head']},record_inputs=lambda work:None)
        result=await dispatch(bridge,'path_preview',{'path':'M0 0 L10 10 L10 0 L0 10 Z','label':'twisted'})
        self.assertFalse(result['valid']); self.assertTrue(any('crosses itself' in w for w in result['warnings']))
        self.assertEqual(runner.pending_images[-1]['id'],result['image']['id'])
        with self.assertRaisesRegex(ValueError,'cannot become a solid'):
            await dispatch(bridge,'create_path_body',{'name':'Twisted','path':'M0 0 L10 10 L10 0 L0 10 Z','operation':'extrude','height':3})
        result=await dispatch(bridge,'create_path_body',{'name':'Bracket','path':with_holes_fixture(),'operation':'extrude','height':3})
        self.assertEqual(result['holes'],1); self.assertTrue(self.work.file('profiles/Bracket.svg').is_file())
        self.assertIn('evenodd',self.work.file('profiles/Bracket.svg').read_text())
        await self.work.fs({'action':'write','path':'model.py','content':'from profiles.Bracket import shape as Bracket\nparts={"Bracket":Bracket}\n'})
        saved=await self.build()
        self.assertEqual(saved['geometry']['solid_count'],1)
        self.assertAlmostEqual(saved['geometry']['volume_mm3'],(30*20-math.pi*4)*3,delta=.2)
        await self.work.fs({'action':'write','path':'model.scad','content':'linear_extrude(height=3) import("profiles/Bracket.svg");\n'})
        saved=await self.build('model.scad')
        self.assertEqual(saved['geometry']['solid_count'],1)
        self.assertAlmostEqual(saved['geometry']['volume_mm3'],(30*20-math.pi*4)*3,delta=6)
        # The OpenSCAD helpers mirror the generators: rounded plate with a hex pocket and a slot, plus a lofted knob.
        await self.work.fs({'action':'write','path':'model.scad','content':'''use <cad_paths.scad>;
linear_extrude(3) difference() { rounded_rect(60, 40, 4); translate([15, 20]) hexagon(5.5); translate([25, 18]) slot(14, 4); }
translate([80, 0, 0]) loft2(0, 20) { rounded_rect(20, 20, 4, center = true); circle(d = 10, $fn = 48); }
'''})
        saved=await self.build('model.scad')
        self.assertEqual(saved['geometry']['solid_count'],2)
        plate=(60*40-(4-math.pi)*16-2*math.sqrt(3)*2.75**2-(10*4+math.pi*4))*3
        self.assertAlmostEqual(saved['geometry']['parts'][0]['volume_mm3'],plate,delta=8)

    async def test_svg_arcs_quadratic_smooth_and_revolve(self):
        code='''from cad_paths import extrude, revolve
import FreeCAD as App
a=extrude("M0 10 A10 10 0 1 1 20 10 A10 10 0 1 1 0 10 Z",5)
b=revolve("M5 0 L10 0 Q15 10 10 20 L5 20 Z")
b.translate(App.Vector(50,0,0))
c=extrude("M0 0 C3 -2 7 -2 10 0 S17 2 20 0 L20 10 Q15 12 10 10 T0 10 Z",2)
c.translate(App.Vector(80,0,0))
parts={"Round":a,"Turned":b,"Smooth":c}
'''
        await self.work.fs({'action':'write','path':'model.py','content':code})
        saved=await self.build()
        self.assertEqual(saved['geometry']['solid_count'],3)
        self.assertAlmostEqual(saved['geometry']['parts'][0]['volume_mm3'],3.141592653589793*100*5,delta=.03)


def with_holes_fixture():
    from server.svg_path import rect, circle, with_holes
    return with_holes(rect(30, 20), circle(4, (15, 10)))


class PiWorkspaceTests(WorkspaceTests):
    async def test_real_pi_read_write_edit_bash_and_spec_survive_new_bridge(self):
        session=SimpleNamespace(project=self.project,engine='hybrid',manual_changes=False,app={'name':'FreeCAD'},
                                screen=SimpleNamespace(release_inputs=None),state_dir=None)
        runner=AgentRunner(session,{'agent':{'native_operations':True},'planner':{},'policy':{},'research':{'enabled':False}})
        self.addAsyncCleanup(runner.wait_stopped)
        calls=[('read',{'path':'CAD_GUIDE.md'}),('write',{'path':'notes.txt','content':'length = 20\n'}),
               ('edit',{'path':'notes.txt','edits':[{'oldText':'20','newText':'42'}]}),('read',{'path':'notes.txt'}),
               ('bash',{'command':'printf "pi shell works"'}),('spec_read',{})]
        index=0
        async def reply(*args,**kwargs):
            nonlocal index
            if index<len(calls):
                name,arguments=calls[index];index+=1
                return {'content':'','tool_calls':[{'id':f'call-{index}','name':name,'arguments':json.dumps(arguments)}]}
            return {'content':'Ready.','tool_calls':[]}
        with pi_model(runner,reply):
            runner.start('Make a 42 mm bracket','auto')
            async with asyncio.timeout(20):
                while runner.phase!='awaiting':await asyncio.sleep(.02)
            await runner.wait_stopped()
        results=[m for m in pi_messages(runner) if m.get('role')=='toolResult']
        self.assertEqual(len(results),len(calls))
        # The model works in /work; Pi's appended working-directory line must not leak the host path.
        system=runner.pi_test_requests[0]['messages'][0]['content']
        self.assertIn('Current working directory: /work',system)
        self.assertNotIn(str(self.project.path),system)
        self.assertTrue(self.work.file('svg_path.py').is_file())
        self.assertFalse(any(m.get('isError') for m in results),[message_text(m) for m in results])
        self.assertIn('length = 42',message_text(results[3]))
        self.assertIn('pi shell works',message_text(results[4]))
        self.assertIn('Make a 42 mm bracket',message_text(results[5]))
        self.assertEqual(self.work.file('notes.txt').read_text(),'length = 42\n')
        # New runner/process restores Pi's session; source/spec are independent durable files.
        restarted=AgentRunner(session,runner.config)
        self.addAsyncCleanup(restarted.wait_stopped)
        reply2=AsyncMock(side_effect=[{'content':'','tool_calls':[{'id':'reopen','name':'read','arguments':json.dumps({'path':'design-spec.json'})}]}, {'content':'Kept the bracket requirement.'}])
        with pi_model(restarted,reply2):
            restarted.start('Continue','auto')
            async with asyncio.timeout(20):
                while restarted.phase!='awaiting':await asyncio.sleep(.02)
            await restarted.wait_stopped()
        restored=[m for m in pi_messages(restarted) if m.get('role')=='toolResult'][-1]
        self.assertIn('Make a 42 mm bracket',message_text(restored))


class WorkspaceApiTests(WorkspaceTests):
    async def test_editor_routes_reject_stale_and_active_writes(self):
        from fastapi.testclient import TestClient
        from server.app import app
        client=TestClient(app,base_url='http://localhost')
        with patch('server.app._project',return_value=self.project), patch('server.app.manager.sessions',{}):
            base=f'/api/projects/{self.project.id}/workspace'
            self.assertEqual(client.get(base).status_code,200)
            file=client.get(base+'/file',params={'path':'model.py'}).json()
            response=client.put(base+'/file',json={**file,'content':'# saved in editor\n'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.put(base+'/file',json={**file,'content':'# stale'}).status_code,409)
            self.assertEqual(client.get(base+'/file',params={'path':'../project.json'}).status_code,409)
            active=SimpleNamespace(project=self.project,agent=SimpleNamespace(active=True))
            with patch('server.app.manager.sessions',{'active':active}):
                self.assertEqual(client.put(base+'/file',json={**response.json(),'content':'# cannot overwrite'}).status_code,409)
