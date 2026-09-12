from pi_fixture import pi_model, messages as pi_messages, message_text
"""Vibe-modeling capabilities: profile solids, transforms, views, references, knowledge, images."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner
from server.attachments import image_path, store_image
from server.design import scad_source
from server.knowledge import design_notes, recall_facts, remember_facts
from server.operations import OPERATION_SCHEMA, candidate, validate_operation, workspace
from server.projects import Project, atomic_json
from server.research import validate_notes, source
from test_projects import stage_fixture


def op(tool, plan='', **arguments):
    return {'plan': plan, 'tool': tool, 'arguments': arguments}


def params(**values):
    return [{'name': k, 'value': v} for k, v in values.items()]


def plate_ops():
    return [op('create_body', id='plate', name='Plate', kind='extrude', profile=[['0', '0'], ['L', '0'], ['L', 'W'], ['0', 'W']], height='t',
               at=['0', '0', '0'], axis='z', parameters=params(L=40, W=30, t=5)),
            op('fillet', body='plate', radius='3', edges='vertical', parameters=[]),
            op('fuse', body='plate', kind='revolve', profile=[['0', '0'], ['6', '0'], ['6', '8'], ['0', '8']], angle='360', at=['20', '15', '4'], axis='z', parameters=[]),
            op('polar_pattern', body='plate', count=4, axis='z', center=['20', '15', '0'],
               operation=op('cut', body='plate', kind='cylinder', dimensions=['1.5', '20'], at=['30', '15', '-5'], anchor='base', axis='z', parameters=[])),
            op('mirror', body='plate', plane='x', at='0', parameters=[]),
            op('fuse', body='plate', kind='text', text='Pi', size='6', height='2', at=['2', '20', '4'], axis='z', parameters=[]),
            op('fuse', body='plate', kind='pipe', path=[['-30', '5', '4'], ['-30', '5', '12'], ['-15', '5', '12']], radius='1.5', parameters=[]),
            op('fuse', body='plate', kind='loft', sections=[{'z': '0', 'profile': [['0', '0'], ['8', '0'], ['8', '8'], ['0', '8']]},
                                                             {'z': '6', 'profile': [['2', '2'], ['6', '2'], ['6', '6'], ['2', '6']]}], at=['-20', '18', '4'], axis='z', parameters=[])]


class ContractTests(unittest.TestCase):
    def test_new_kinds_compile_and_report_bounds(self):
        saved = workspace()
        for item in plate_ops():
            saved, design, state = candidate(saved, item)
        self.assertEqual(state['bodies']['plate']['bounds_mm']['x'], [-40, 40])
        kinds = {f['kind'] for f in design['features']}
        self.assertTrue({'extrude', 'fillet', 'revolve', 'rotate', 'mirror', 'text', 'pipe', 'loft'} <= kinds)
        self.assertIn('linear_extrude', scad_source(design))
        self.assertIn('rotate_extrude', scad_source(design))
        self.assertLess(len(json.dumps(OPERATION_SCHEMA)), 140000)
        with self.assertRaisesRegex(ValueError, 'degenerate'):
            candidate(workspace(), op('create_body', id='x', name='x', kind='extrude', profile=[['0', '0'], ['1', '0'], ['2', '0']], height='1', at=['0', '0', '0'], axis='z', parameters=params(k=1)))
        with self.assertRaisesRegex(ValueError, 'same body'):
            validate_operation(op('polar_pattern', body='plate', count=3, axis='z', center=['0', '0', '0'],
                                  operation=op('cut', body='other', kind='sphere', dimensions=['1'], at=['0', '0', '0'], anchor='center', axis='z', parameters=[])))
        saved, design, state = candidate(saved, op('place_reference', id='board', file='pi3b.step', at=['5', '5', '3'], parameters=[]))
        self.assertEqual(state['references']['board']['file'], 'pi3b.step')
        self.assertEqual([f for f in design['features'] if f['kind'] == 'reference'][0]['options'], {'file': 'pi3b.step'})

    def test_learned_facts_are_remembered_from_read_pages_only(self):
        from server import knowledge
        with tempfile.TemporaryDirectory() as root, patch.object(knowledge, 'LEARNED', Path(root) / 'learned.jsonl'):
            self.assertEqual(recall_facts('acme widget mounting holes')['facts'], [])
            page = {'url': 'https://example.com/widget.pdf', 'title': 'Acme widget drawing'}
            added = remember_facts([{'statement': 'Acme widget mounting holes are 30 mm apart', 'quote': '30 mm'}], page, 'p1')
            self.assertEqual(added, 1)
            self.assertEqual(remember_facts([{'statement': 'Acme widget mounting holes are 30 mm apart', 'quote': '30 mm'}], page), 0)
            found = recall_facts('acme widget holes')
            self.assertEqual(found['facts'][0]['source_url'], page['url'])
            self.assertEqual(recall_facts('unrelated fan')['facts'], [])
        notes = design_notes('enclosure wall thickness for FDM')
        self.assertEqual(notes['notes'][0]['document'], 'fdm_enclosures')
        self.assertIn('Wall thickness', notes['notes'][0]['text'])

    def test_image_store_rejects_non_images_and_bounds_size(self):
        with tempfile.TemporaryDirectory() as root:
            buffer = io.BytesIO(); Image.new('RGB', (3000, 1500), 'red').save(buffer, 'PNG')
            stored = store_image(Path(root) / 'attachments', buffer.getvalue(), 'photo.png')
            self.assertEqual(stored['width'], 2000)
            self.assertEqual(image_path(root, stored['id']).name, stored['id'])
            with self.assertRaisesRegex(ValueError, 'Not a readable image'):
                store_image(Path(root) / 'attachments', b'not an image', 'x')
            with self.assertRaises(ValueError):
                image_path(root, '../etc/passwd')
        self.assertEqual(validate_notes({'facts': [], 'assumptions': [], 'unknowns': []}, [source('https://x.example/a.jpg', 'pic', 'pic', 'search_result')]),
                         {'facts': [], 'assumptions': [], 'unknowns': []})


class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_solids_transforms_views_faces_and_reference_clearance(self):
        with tempfile.TemporaryDirectory(prefix='capabilities-') as root:
            project = Project.create(root, 'freecad')
            saved = workspace()
            for item in plate_ops():
                saved, design, state = candidate(saved, item)
            stage = await project.prepare(design)
            atomic_json(stage / 'workspace.json', saved)
            result = project.commit(stage, None)
            geometry = result['geometry']
            self.assertEqual(geometry['solid_count'], 1)
            self.assertEqual(geometry['views'], ['iso', 'top', 'front', 'right'])
            self.assertGreater(len(geometry['faces']), 20)
            self.assertEqual(len(geometry['cuts']), 4)
            with Image.open(project.file('r0001', 'view-iso.png')) as picture:
                self.assertEqual(picture.size, (560, 560))
            # A reference STL placed above the plate reports clearance; overlapping reports interference.
            stl = Path(root) / 'block.stl'
            stl.write_text(cube_stl())
            project.add_reference('block.stl', stl.read_bytes())
            saved2, design2, _ = candidate(saved, op('place_reference', id='block', file='block.stl', at=['0', '0', '20'], parameters=[]))
            stage2 = await project.prepare(design2)
            report = json.loads((stage2 / 'geometry.json').read_text())
            self.assertEqual(report['references'][0]['file'], 'block.stl')
            # Nearest solid point is on the boss top rim: (14.63, 12.32, 12) to the block corner (10, 10, 20).
            self.assertAlmostEqual(report['references'][0]['parts'][0]['clearance_mm'], 9.531, places=2)
            self.assertEqual(report['references'][0]['parts'][0]['interference_mm3'], 0)
            saved3, design3, _ = candidate(saved, op('place_reference', id='block', file='block.stl', at=['0', '0', '2'], parameters=[]))
            report3 = json.loads(((await project.prepare(design3)) / 'geometry.json').read_text())
            self.assertGreater(report3['references'][0]['parts'][0]['interference_mm3'], 100)
            with self.assertRaisesRegex(ValueError, 'not in this project'):
                await project.prepare(candidate(saved, op('place_reference', id='m', file='missing.step', at=['0', '0', '0'], parameters=[]))[1])


def cube_stl(size=10.0):
    v = [(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0), (0, 0, size), (size, 0, size), (size, size, size), (0, size, size)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    lines = ['solid cube']
    for a, b, c in faces:
        lines.append(' facet normal 0 0 0\n  outer loop')
        for i in (a, b, c):
            lines.append(f'   vertex {v[i][0]} {v[i][1]} {v[i][2]}')
        lines.append('  endloop\n endfacet')
    lines.append('endsolid cube')
    return '\n'.join(lines)


class AgentImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='capabilities-agent-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        screen = SimpleNamespace(capture_image=lambda: Image.new('RGB', (64, 64), '#333'))
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}, screen=screen, state_dir=None),
                                  {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}, 'research': {'enabled': True}})
        self.runner.task_text = 'make a knob'
        self.runner.active = True

    def test_views_attachments_and_selection_reach_the_modeling_turn(self):
        saved, design, state = candidate(workspace(), op('create_body', id='k', name='Knob', kind='cylinder', dimensions=['10', '8'], at=['0', '0', '0'], anchor='base', axis='z', parameters=params(d=1)))
        views = self.project.path / '.build-views'; views.mkdir()
        for name in ('design.json', 'geometry.json', 'model.FCStd', 'model.scad', 'model.step', 'model.stl'):
            (views / name).write_text('x')
        atomic_json(views / 'design.json', design)
        Image.new('RGB', (560, 560), 'white').save(views / 'view-iso.png'); Image.new('RGB', (560, 560), 'white').save(views / 'view-top.png')
        geometry = {'valid_solid': True, 'solid_count': 1, 'volume_mm3': 1, 'bounds_mm': [1, 1, 1], 'min_mm': [0, 0, 0], 'max_mm': [1, 1, 1], 'cuts': [],
                    'result_object': 'Op001', 'views': ['iso', 'top'], 'faces': [{'index': 1, 'normal': [0, 0, 1], 'min_mm': [0, 0, 8], 'max_mm': [20, 20, 8], 'area_mm2': 314}]}
        atomic_json(views / 'geometry.json', geometry)
        public = self.project.commit(views, None)
        buffer = io.BytesIO(); Image.new('RGB', (200, 100), 'blue').save(buffer, 'PNG')
        stored = store_image(self.project.path / 'attachments', buffer.getvalue(), 'sketch.png')
        self.runner._accept_attachments([stored['id'], 'nope.jpg'])
        self.runner._last_selection = [{'document': 'Model', 'object': 'Op001', 'subelements': ['Face1']}]
        ctx = {'project': self.project, 'expected_head': public['head'], 'state': state, 'ledger': saved, 'geometry': public['geometry']}
        messages = self.runner._native_messages([{'role': 'user', 'content': 'knob'}], ctx)
        content = messages[-1]['content']
        self.assertIsInstance(content, list)
        self.assertEqual(sum(1 for part in content if part['type'] == 'image_url'), 3)
        payload = json.loads(content[0]['text'])
        self.assertEqual(payload['attached_images'][1:3], ['saved model view: iso (r0001)', 'saved model view: top (r0001)'])
        self.assertEqual(payload['user_selection']['items'][0]['faces'][0]['index'], 1)
        self.assertEqual(payload['geometry']['views_attached'], ['iso', 'top'])

    async def test_research_images_and_pdf_pages_become_pending_images(self):
        picture = io.BytesIO(); Image.new('RGB', (300, 200), 'green').save(picture, 'JPEG')
        with patch.object(self.runner.research_tool, 'images', AsyncMock(return_value={'operation': 'images', 'query': 'pi 3b top view', 'notice': 'illustrations only',
                                                                                           'pictures': [{'url': 'https://example.com/pi.jpg', 'title': 'Pi 3B top', 'page': None, 'bytes': picture.getvalue(), 'content_type': 'image/jpeg'}]})):
            await self.runner._research_images_step('pi 3b top view')
        self.assertEqual(len(self.runner.pending_images), 1)
        self.assertTrue(self.runner.pending_images[0]['label'].startswith('reference image: Pi 3B top'))
        result = next(e for e in self.runner.events if e['t'] == 'research_result')
        self.assertEqual(result['sources'][0]['kind'], 'image')
        self.assertTrue(result['sources'][0]['preview'].startswith('/api/projects/'))
        page = io.BytesIO(); Image.new('RGB', (800, 1000), 'white').save(page, 'JPEG')
        read = {'operation': 'read', 'sources': [source('https://example.com/drawing.pdf', 'drawing.pdf', '(This PDF has no extractable text)', 'pdf')], 'notice': 'x', 'page_images': [{'page': 1, 'jpeg': page.getvalue()}]}
        with patch.object(self.runner.research_tool, 'read', AsyncMock(return_value=read)), patch.object(self.runner, '_chat', AsyncMock(return_value='{"facts":[],"assumptions":[],"unknowns":[]}')):
            await self.runner._research_step('https://example.com/drawing.pdf', 'ports')
        self.assertEqual(len(self.runner.pending_images), 2)
        self.assertIn('page 1 of drawing.pdf', self.runner.pending_images[-1]['label'])
        self.assertNotIn('page_images', next(e for e in self.runner.events if e['t'] == 'research_result' and e.get('operation') == 'read'))
        ctx = {'project': self.project, 'expected_head': None, 'state': {'bodies': {}}, 'ledger': workspace(), 'geometry': None}
        messages = self.runner._native_messages([{'role': 'user', 'content': 'x'}], ctx, research_pages=True)
        self.assertEqual(sum(1 for part in messages[-1]['content'] if part['type'] == 'image_url'), 2)

    async def test_knowledge_and_reference_tools_in_the_loop(self):
        stl = Path(self.tmp.name) / 'board.stl'; stl.write_text(cube_stl(5))
        self.project.add_reference('board.stl', stl.read_bytes())
        from server import knowledge
        self.learned = tempfile.NamedTemporaryFile(suffix='.jsonl', delete=False); self.learned.close()
        Path(self.learned.name).write_text(json.dumps({'statement': 'Widget board is 85 x 56 mm with holes 58 x 49 mm apart', 'quote': '58 x 49', 'source_url': 'https://example.com/w', 'source_title': 'Widget drawing', 'added_at': 1}) + '\n')
        self.addCleanup(lambda: Path(self.learned.name).unlink(missing_ok=True))
        patcher = patch.object(knowledge, 'LEARNED', Path(self.learned.name)); patcher.start(); self.addCleanup(patcher.stop)
        from test_tooling_v2 import tool_turns, tool_results
        chat = tool_turns(self.runner, ('Checking what I know.', [('recall_facts', {'query': 'widget board holes'}), ('design_notes', {'topic': 'fdm enclosure walls'}),
                                                                  ('import_reference', {'url_or_name': 'board.stl'})]), ('Ready.', []))
        with pi_model(self.runner, chat):
            self.assertTrue(await self.runner._native_model('pi case'))
        recalled, notes, reference = tool_results(self.runner, 1)
        self.assertEqual(recalled['facts'][0]['source_url'], 'https://example.com/w')
        self.assertTrue(any('58' in f for f in self.runner.library_facts))
        self.assertEqual(notes['notes'][0]['document'], 'fdm_enclosures')
        self.assertEqual(reference['file'], 'board.stl'); self.assertEqual(reference['references'], ['board.stl'])
        self.assertEqual(self.runner._grounding_caveat('The pattern is 58 x 49 mm'), '')
        self.assertEqual(json.loads((self.project.path / 'conversation.json').read_text())['library_facts'], self.runner.library_facts)


if __name__ == '__main__':
    unittest.main()


class PersistentLoopTests(unittest.IsolatedAsyncioTestCase):
    """Native projects: one loop holds the conversation; small edits are single tool calls."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='persistent-loop-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        screen = SimpleNamespace(capture_image=lambda: Image.new('RGB', (64, 64), '#333'), release_inputs=None)
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}, screen=screen, state_dir=None),
                                  {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}, 'research': {'enabled': False}})

    async def test_follow_up_edits_are_one_tool_call_without_a_planner(self):
        from test_tooling_v2 import tool_turns, tool_results
        chat = tool_turns(self.runner,
                          ('Box 60x40x30 (user), wall 2 mm (user).', [('create_body', {'id': 'box', 'name': 'Box', 'kind': 'box', 'dimensions': ['L', 'W', 'H'], 'parameters': params(L=60, W=40, H=30, wall=2)}),
                                                                        ('shell', {'body': 'box', 'wall': 'wall', 'open_faces': ['zmax']})]),
                          ('Open-top box saved.', []),
                          ('Setting wall to 1.2.', [('set_parameter', {'name': 'wall', 'value': 1.2})]),
                          ('Walls are now 1.2 mm; outer size unchanged.', []),
                          ('The walls are 1.2 mm thick; inside is 57.6 x 37.6 mm.', []))
        async def prepare(design):
            return stage_fixture(self.project, design)
        async def drive():
            async with asyncio.timeout(10):
                while sum(1 for e in self.runner.events if e['t'] == 'done') < 1:
                    await asyncio.sleep(0.01)
                self.runner.submit_intent('make the walls thinner, like 1.2')
                while sum(1 for e in self.runner.events if e['t'] == 'done') < 2:
                    await asyncio.sleep(0.01)
                self.runner.submit_intent('how thick are they now?')
                while sum(1 for e in self.runner.events if e['t'] == 'done') < 3:
                    await asyncio.sleep(0.01)
                self.runner.stop()
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), \
             patch('server.agent.present_revision', return_value=None), patch.object(self.runner, '_plan_intent', AsyncMock(side_effect=AssertionError('planner must not run'))):
            self.runner.start('make a small open-top box 60x40x30 with 2 mm walls', 'auto')
            await drive()
            await self.runner.wait_stopped()
        self.assertEqual(self.project.read()['head'], 'r0003')
        self.assertEqual(self.project.public()['workspace']['parameters']['wall'], 1.2)
        kinds = [e['t'] for e in self.runner.events]
        self.assertGreaterEqual(kinds.count('done'), 3)
        self.assertIn('await_intent', kinds)
        self.assertNotIn('planning', kinds)
        # The third request carries the whole transcript: user, assistant with tool calls, tool results, user again.
        roles = [m['role'] for m in self.runner.calls[2]]
        self.assertEqual(roles[:6], ['system', 'user', 'assistant', 'tool', 'tool', 'assistant'])
        self.assertEqual(message_text(self.runner.calls[2][-1]), 'make the walls thinner, like 1.2')
        self.assertEqual(tool_results(self.runner, 2)[-1]['workspace']['parameters']['wall'], 2.0)
        self.assertEqual(tool_results(self.runner, 3)[-1]['workspace']['parameters']['wall'], 1.2)
        self.assertEqual(self.runner.dialogue.context()[-1]['content'], 'The walls are 1.2 mm thick; inside is 57.6 x 37.6 mm.')
        saved = json.loads((self.project.path / 'conversation.json').read_text())
        self.assertEqual(saved['pi_session']['runtime'], 'pi-coding-agent')
        self.assertEqual(pi_messages(self.runner)[-1]['role'], 'assistant')

    async def test_message_during_a_turn_is_folded_in_not_dropped(self):
        started = asyncio.Event()
        replies = iter([('Noted, changing the width.', []), ('Done.', [])])
        async def chat(endpoint, messages, tools, **kwargs):
            if not started.is_set():
                started.set()
                await asyncio.sleep(.2)  # Pi delivers steering at its next boundary
            content, calls = next(replies)
            return {'content': content, 'tool_calls': [], 'reasoning': '', 'finish_reason': 'stop', 'streams': []}
        async def drive():
            async with asyncio.timeout(10):
                await started.wait()
                self.runner.submit_intent('actually make it 50 wide')
                while not any(e['t'] == 'done' for e in self.runner.events):
                    await asyncio.sleep(0.01)
                self.runner.stop()
        with pi_model(self.runner, chat), patch.object(self.runner, '_plan_intent', AsyncMock(side_effect=AssertionError('planner must not run'))):
            self.runner.start('make a 40 wide plate', 'auto')
            await drive()
            await self.runner.wait_stopped()
        self.assertEqual([message_text(m) for m in pi_messages(self.runner) if m['role'] == 'assistant'], ['Noted, changing the width.', 'Done.'])
        self.assertEqual([e['text'] for e in self.runner.events if e['t'] == 'user'], ['make a 40 wide plate', 'actually make it 50 wide'])
        self.assertEqual([message_text(m) for m in pi_messages(self.runner) if m['role'] == 'user'], ['make a 40 wide plate', 'actually make it 50 wide'])


class ParametersFirstTests(unittest.IsolatedAsyncioTestCase):
    """Declaring parameters before any body, expression parameters, and one failure per response."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='params-first-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}),
                                  {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}})
        self.runner.task_text = 'pi zero case'

    async def test_parameter_without_a_body_survives_pi_restart(self):
        from test_tooling_v2 import tool_turns, tool_results
        chat = tool_turns(self.runner, ('', [('define_parameter', {'name': 'board_l', 'value': 65})]), ('Parameter saved.', []))
        with pi_model(self.runner, chat):
            await self.runner._native_model('Remember the board length')
        self.runner = AgentRunner(self.runner.session, self.runner.config)
        chat = tool_turns(self.runner, ('', [('inspect', {})]), ('The board length is still 65 mm.', []))
        with pi_model(self.runner, chat):
            await self.runner._native_model('Inspect the workspace')
        self.assertEqual(tool_results(self.runner, 1)[-1]['workspace']['parameters']['board_l'], 65)

    async def test_parameters_declared_before_the_first_body_are_kept_and_used(self):
        from test_tooling_v2 import tool_turns, tool_results
        chat = tool_turns(self.runner,
                          ('Declaring the dimensions first.', [('define_parameter', {'name': 'board_l', 'value': 65}), ('define_parameter', {'name': 'clearance', 'value': 1.5}),
                                                               ('define_parameter', {'name': 'wall', 'value': 2}), ('define_parameter', {'name': 'case_l', 'value': 'board_l + 2*clearance + 2*wall'})]),
                          ('Now the base.', [('create_body', {'id': 'base', 'name': 'Base', 'kind': 'box', 'dimensions': ['case_l', '37', '10']})]),
                          ('Base saved.', []))
        async def prepare(design):
            return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('pi zero case'))
        results = tool_results(self.runner, 1)
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(results[-1]['workspace']['parameters']['case_l'], 'board_l + 2*clearance + 2*wall = 72')
        self.assertIsNone(results[-1]['head'])
        self.assertEqual(self.project.read()['head'], 'r0001')
        saved_parameters = {p['name']: p['value'] for p in self.project.current_design()['parameters']}
        self.assertEqual(saved_parameters['case_l'], 72.0)  # the expression parameter reached the built design
        self.assertEqual(self.project.public()['workspace']['parameters']['case_l'], 'board_l + 2*clearance + 2*wall')
        self.assertFalse(any(e['t'] == 'tool_error' for e in self.runner.events))

    async def test_failed_tools_report_errors_and_model_can_repair_the_design(self):
        from test_tooling_v2 import tool_turns, tool_results
        self.runner.config['agent']['native_failure_limit'] = 2
        bad = {'id': 'base', 'name': 'Base', 'kind': 'box', 'dimensions': ['nope', '30', '10']}
        chat = tool_turns(self.runner,
                          ('Building.', [('create_body', bad), ('shell', {'body': 'base', 'wall': '2', 'open_faces': ['zmax']}), ('hole', {'body': 'base', 'face': 'zmin', 'u': 'center', 'v': 'center', 'diameter': '3', 'depth': 'through'})]),
                          ('Fixing.', [('create_body', {'id': 'base', 'name': 'Base', 'kind': 'box', 'dimensions': ['65', '30', '10']})]),
                          ('Done.', []))
        async def prepare(design):
            return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('case'))
        results = tool_results(self.runner, 1)
        self.assertEqual(len(results), 3)
        self.assertFalse(results[0]['ok']); self.assertIn('unknown name nope', results[0]['error'])
        self.assertFalse(results[1]['ok']); self.assertFalse(results[2]['ok'])
        self.assertEqual(sum(1 for e in self.runner.events if e['t'] == 'tool_error'), 3)
        self.assertEqual(self.project.read()['head'], 'r0001')  # the turn survived: one failure, not three
