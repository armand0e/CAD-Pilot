import asyncio
import copy
import json
import math
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from fastapi.testclient import TestClient
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.design import validate_design, expression, scad_source, geometry_signature
from server.projects import Project, atomic_json, FILES
from server.agent import AgentRunner, GuidanceChanged
from server.app import app


def block():
    return {'name':'Test part', 'parameters':[{'name':'length','value':60}, {'name':'width','value':40}, {'name':'height','value':8}],
            'features':[{'id':'Stock','kind':'box','dimensions':['length','width','height'],
                         'position':['0','0','0'],'rotation':[0,0,0],'inputs':[]}], 'result':'Stock'}


def stage_fixture(project, design=None):
    stage = Path(tempfile.mkdtemp(prefix='.build-', dir=project.path))
    for name in FILES:
        (stage / name).write_text('fixture, not a real CAD file')
    atomic_json(stage / 'design.json', design or block())
    atomic_json(stage / 'geometry.json', {'valid_solid':True, 'solid_count':1, 'bounds_mm':[60,40,8]})
    return stage


class DesignTests(unittest.TestCase):
    def test_renaming_does_not_evade_failed_geometry_guard(self):
        first=block();second=block();second['name']='Attempt two'
        second['features'][0]['id']='Renamed';second['result']='Renamed'
        second['parameters'][0]['name']='len_mm';second['features'][0]['dimensions'][0]='len_mm'
        self.assertEqual(geometry_signature(first),geometry_signature(second))
        second['features'][0]['position'][0]='1'
        self.assertNotEqual(geometry_signature(first),geometry_signature(second))

    def test_expressions_are_arithmetic_only(self):
        self.assertEqual(expression('width - 2 * wall', {'width':40,'wall':3})[0], 34)
        for text in ['__import__("os")', 'width.__class__', '1e999', 'True', '[1][0]', '2 ** 50', '1/0']:
            with self.subTest(text=text), self.assertRaises((ValueError, SyntaxError)):
                expression(text, {'width':40})

    def test_schema_rejects_unknown_fields_and_missing_position(self):
        for change in [lambda d:d.update(script='bad'), lambda d:d['features'][0].pop('position')]:
            d=block(); change(d)
            with self.assertRaises(ValueError): validate_design(d)

    def test_graph_rejects_disconnected_or_forward_references(self):
        d=block(); d['features'].append(dict(d['features'][0], id='Unused'))
        with self.assertRaises(ValueError): validate_design(d)
        d=block(); d['features'][0].update(kind='difference', dimensions=[], inputs=['Stock','Future'])
        with self.assertRaises(ValueError): validate_design(d)

    def test_zero_negative_and_nonfinite_dimensions_rejected(self):
        for value in [0,-1,float('nan'),float('inf'),True,100001]:
            d=block(); d['parameters'][0]['value']=value
            with self.assertRaises(ValueError): validate_design(d)

    def test_unused_parameters_cannot_pretend_to_control_geometry(self):
        d=block();d['parameters'].append({'name':'hole_diameter','value':5})
        with self.assertRaisesRegex(ValueError,'Unused parameters: hole_diameter'):validate_design(d)

    def test_source_preserves_parametric_relations_without_code_injection(self):
        d=block(); d['features'][0]['position'][0]='length/2'
        self.assertIn('(length / 2)', scad_source(d))
        d['parameters'][0]['name']='x;echo(1)'
        with self.assertRaises(ValueError): scad_source(d)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cadpilot-store-test-')
        self.addCleanup(self.tmp.cleanup)
        self.project=Project.create(self.tmp.name,'freecad')

    def test_commit_restore_reopen_and_preserve_old_files(self):
        p=self.project
        p.commit(stage_fixture(p),None)
        first=p.file('r0001','model.FCStd').read_bytes()
        d=block();d['parameters'][0]['value']=80
        p.commit(stage_fixture(p,d),'r0001')
        p.restore('r0001','r0002')
        self.assertEqual(p.read()['head'],'r0003')
        self.assertEqual(len(p.read()['revisions']),3)
        self.assertEqual(p.file('r0001','model.FCStd').read_bytes(),first)
        self.assertEqual(Project(self.tmp.name,p.id).current_design()['parameters'][0]['value'],60)

    def test_stale_commit_and_restore_are_rejected(self):
        p=self.project;p.commit(stage_fixture(p),None)
        with self.assertRaisesRegex(ValueError,'changed during generation'): p.commit(stage_fixture(p),None)
        with self.assertRaises(ValueError):p.restore('r0001',None)
        self.assertEqual(p.read()['head'],'r0001')

    def test_downloads_reject_traversal_unknown_files_symlinks_and_tampering(self):
        p=self.project;p.commit(stage_fixture(p),None)
        for revision,name in [('../','model.FCStd'),('r0001','../../project.json'),('r0001','compiler.log')]:
            with self.assertRaises(ValueError):p.file(revision,name)
        artifact=p.path/'r0001/model.FCStd';artifact.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'integrity'):p.file('r0001','model.FCStd')
        artifact.unlink();artifact.symlink_to(p.path/'project.json')
        with self.assertRaises(ValueError):p.file('r0001','model.FCStd')

    def test_uncommitted_crash_directory_never_overwritten(self):
        p=self.project;(p.path/'r0001').mkdir()
        p.commit(stage_fixture(p),None)
        self.assertEqual(p.read()['head'],'r0002')


class NativeAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cadpilot-agent-test-')
        self.addCleanup(self.tmp.cleanup)
        self.project=Project.create(self.tmp.name,'freecad')
        self.runner=AgentRunner(SimpleNamespace(project=self.project,engine='hybrid',manual_changes=False,
                               app={'name':'FreeCAD'}), {'agent':{'native_operations': False}, 'planner':{}, 'policy':{}})
        self.runner.task_text='Make a block'

    async def test_steering_during_final_document_check_prevents_stale_commit(self):
        r = self.runner
        checking = asyncio.Event()
        calls = 0
        async def guard(session):
            nonlocal calls
            calls += 1
            if calls == 2:
                checking.set()
                await asyncio.Event().wait()
            return None
        async def chat(*args, **kwargs):
            return json.dumps(block())
        async def prepare(design):
            return stage_fixture(self.project, design)
        with patch('server.agent.native_edit_blocker', guard), patch.object(r, '_chat', chat), patch.object(self.project, 'prepare', prepare):
            task = asyncio.create_task(r._native_model('Create the block'))
            await asyncio.wait_for(checking.wait(), 2)
            r._guidance_changed.set()
            with self.assertRaises(GuidanceChanged):
                await asyncio.wait_for(task, 2)
        self.assertIsNone(self.project.read()['head'])
        self.assertFalse(list(self.project.path.glob('.build-*')))

    async def test_failed_tool_error_returns_to_model_before_retry(self):
        r=self.runner; calls=[]
        async def chat(endpoint,messages,**kwargs):
            calls.append(copy.deepcopy(messages))
            design=block()
            if len(calls)>1: design['features'][0]['position'][0]='1'
            return json.dumps(design)
        count=0
        async def prepare(design):
            nonlocal count
            count+=1
            if count==1:raise ValueError('Cutter missed the stock')
            return stage_fixture(self.project)
        with patch.object(r,'_chat',chat),patch.object(self.project,'prepare',prepare),patch('server.agent.present_revision',return_value=None):
            self.assertTrue(await r._native_model('Create the solid'))
        self.assertIn('Cutter missed the stock',calls[1][-1]['content'])
        self.assertEqual(self.project.read()['head'],'r0001')

    async def test_identical_failed_recipe_is_not_reexecuted_and_is_retained(self):
        r=self.runner
        async def chat(*args,**kwargs): return json.dumps(block())
        with patch.object(r,'_chat',chat),patch.object(self.project,'prepare',side_effect=ValueError('Cutter missed the stock')) as prepare:
            self.assertFalse(await r._native_model('Create the solid'))
        self.assertEqual(prepare.call_count,1)
        self.assertIsNone(self.project.read()['head'])
        records=[json.loads(p.read_text()) for p in (self.project.path/'attempts').glob('attempt_*.json')]
        self.assertEqual(len(records),3)
        self.assertTrue(all(record['raw_recipe'] and not record['committed'] for record in records))
        self.assertTrue(any('NOT executed again' in record['error'] for record in records))

    async def test_guidance_cancels_build_and_preserves_head(self):
        r=self.runner; started=asyncio.Event();cancelled=asyncio.Event()
        async def chat(*args,**kwargs):return json.dumps(block())
        async def prepare(design):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with patch.object(r,'_chat',chat),patch.object(self.project,'prepare',prepare):
            task=asyncio.create_task(r._native_model('Create the solid'))
            await started.wait();r._guidance_changed.set()
            with self.assertRaises(GuidanceChanged):await task
        self.assertTrue(cancelled.is_set());self.assertIsNone(self.project.read()['head'])

    async def test_manual_work_guard_does_not_call_model(self):
        self.runner.session.manual_changes=True
        self.assertFalse(await self.runner._native_model('Change length'))
        self.assertTrue(self.runner.paused)
        self.assertIsNone(self.project.read()['head'])

    async def test_visual_mode_cannot_use_native_model(self):
        self.runner.session.engine='visual'
        with self.assertRaisesRegex(ValueError,'visual-only'):await self.runner._native_model('Create')

    async def test_steering_at_prepare_completion_removes_stage_without_committing(self):
        r=self.runner
        async def chat(*args,**kwargs):return json.dumps(block())
        async def prepare(design):
            result=stage_fixture(self.project)
            r._guidance_changed.set()
            return result
        with patch.object(r,'_chat',chat),patch.object(self.project,'prepare',prepare):
            with self.assertRaises(GuidanceChanged):await r._native_model('Create')
        self.assertIsNone(self.project.read()['head'])
        self.assertFalse(list(self.project.path.glob('.build-*')))

    async def test_prepare_cancellation_removes_owned_stage(self):
        started=asyncio.Event()
        async def worker(stage):
            started.set();await asyncio.Event().wait()
        with patch('server.projects.run_worker',worker):
            task=asyncio.create_task(self.project.prepare(block()))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(list(self.project.path.glob('.build-*')))


class NativeKernelTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'apps/freecad-extracted/usr/bin/python').exists(), 'Bundled FreeCAD required')
    async def test_real_kernel_rotation_expressions_cuts_and_disconnected_rejection(self):
        with tempfile.TemporaryDirectory(prefix='cadpilot-kernel-test-') as root:
            p=Project.create(root,'freecad')
            d=block();d['features'][0]['rotation']=[0,0,90]
            stage=await p.prepare(d);report=p.commit(stage,None)['geometry']
            for measured,want in zip(report['bounds_mm'],[40,60,8]):self.assertAlmostEqual(measured,want,places=5)
            d=block()
            d['features'] += [{'id':'Bore','kind':'cylinder','dimensions':['2.5','height+2'],
                               'position':['8','8','-1'],'rotation':[0,0,0],'inputs':[]},
                              {'id':'Result','kind':'difference','dimensions':[],
                               'position':['0','0','0'],'rotation':[0,0,0],'inputs':['Stock','Bore']}]
            d['result']='Result'
            stage=await p.prepare(d);report=p.commit(stage,'r0001')['geometry']
            with zipfile.ZipFile(p.file('r0002','model.FCStd')) as archive:
                gui=ET.fromstring(archive.read('GuiDocument.xml'))
                visible=[v.attrib['name'] for v in gui.findall('./ViewProviderData/ViewProvider')
                         if v.find("./Properties/Property[@name='Visibility']/Bool").attrib['value']=='true']
                self.assertEqual(visible,['Result'])
            self.assertAlmostEqual(report['volume_mm3'],60*40*8-math.pi*2.5**2*8,places=5)
            d['features'][1]['position'][0]='1000'
            with self.assertRaisesRegex(ValueError,'removed no material') as failure:await p.prepare(d)
            self.assertIn('"minimum_distance_mm": 937.5',str(failure.exception))
            self.assertIn('"intersection_mm3": 0.0',str(failure.exception))
            self.assertIn('"tool": {"min_mm"',str(failure.exception))
            d['features'][2]['kind']='union'
            with self.assertRaisesRegex(ValueError,'connected solid'):await p.prepare(d)
            self.assertEqual(p.read()['head'],'r0002')
            self.assertFalse(list(p.path.glob('.build-*')))


class ProjectAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cadpilot-api-test-');self.addCleanup(self.tmp.cleanup)
        self.project=Project.create(self.tmp.name,'freecad');self.project.commit(stage_fixture(self.project),None)
        self.client=TestClient(app, base_url='http://localhost')
        self.root=patch('server.app.PROJECT_ROOT',Path(self.tmp.name));self.root.start();self.addCleanup(self.root.stop)

    def test_download_is_scoped_attachment_and_integrity_checked(self):
        url=f'/api/projects/{self.project.id}/r0001/model.FCStd'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['content-disposition'])
        self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/r0001/project.json').status_code,404)
        (self.project.path/'r0001/model.FCStd').write_text('tampered')
        self.assertEqual(self.client.get(url).status_code,404)

    def test_restore_requires_idle_session_and_current_head(self):
        session=SimpleNamespace(project=self.project,agent=SimpleNamespace(active=True))
        with patch('server.app._session',return_value=session):
            url='/api/sessions/test/project'
            self.assertEqual(self.client.post(url,json={'operation':'restore','revision':'r0001','expected_head':'r0001'}).status_code,409)
            session.agent.active=False
            self.assertEqual(self.client.post(url,json={'operation':'restore','revision':'r0001','expected_head':None}).status_code,409)
            self.assertEqual(self.project.read()['head'],'r0001')

    def test_cross_origin_cannot_restore_or_change_engine(self):
        response=self.client.post('/api/sessions/test/project',json={'operation':'engine','engine':'visual'},headers={'Origin':'https://evil.example'})
        self.assertEqual(response.status_code,403)
