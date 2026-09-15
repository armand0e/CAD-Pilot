from pi_fixture import pi_model, message_text
"""Transactional tool regressions, including real sandboxed FreeCAD geometry."""
import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner
from server.operations import candidate, compile_workspace, normalize_tool_arguments, validate_operation, workspace
from server.projects import Project, atomic_json
from test_projects import block, stage_fixture


def op(tool, **arguments):
    return {'tool': tool, 'arguments': arguments}


def params(**values):
    return [{'name': k, 'value': v} for k, v in values.items()]


def plate():
    return op('create_body', id='Plate', name='Mounting plate', parameters=params(length=60, width=40, height=8),
              kind='box', dimensions=['length', 'width', 'height'], position=['0', '0', '0'], rotation=[0, 0, 0])


def holes():
    return op('cut_pattern', body='Plate', parameters=params(diameter=5, offset=8), kind='cylinder',
              dimensions=['diameter/2', 'height+2'], position=['offset', 'offset', '-1'], rotation=[0, 0, 0],
              count_x=2, count_y=2, pitch_x='length-2*offset', pitch_y='width-2*offset')


def enclosure_ops():
    return [op('create_body', id='Base', name='Rounded electronics enclosure',
               parameters=params(length=90, width=60, height=24, radius=6), kind='rounded_box',
               dimensions=['length', 'width', 'height', 'radius'], position=['0', '0', '0'], rotation=[0, 0, 0]),
            op('hollow', body='Base', wall='wall', floor='floor', parameters=params(wall=2, floor=2)),
            op('side_window', body='Base', face='front', along='20', above='6', width='16', height='8', parameters=[]),
            op('lid', id='Lid', body='Base', thickness='lid_thickness', lip_height='lip', clearance='clearance',
               gap='gap', parameters=params(lid_thickness=2, lip=3, clearance=.2, gap=10))]


class OperationTests(unittest.TestCase):
    def test_ask_shortname_accepts_options_like_ask_question(self):
        # Models frequently emit `ask` instead of `ask_question`; options must not be rejected over the name.
        for tool in ('ask', 'ask_question'):
            args = normalize_tool_arguments(tool, {'question': 'Where does the fan mount?',
                'options': [{'label': 'At the card end', 'description': 'blower'}, 'Over the die'], 'multi_select': False})
            self.assertEqual(args['options'][0], {'label': 'At the card end', 'description': 'blower'})
            self.assertEqual(args['options'][1], {'label': 'Over the die', 'description': ''})
            self.assertIs(args['multi_select'], False)
        # A bare question still validates (options/multi_select are backfilled).
        self.assertEqual(normalize_tool_arguments('ask', {'question': 'Proceed?'}),
                         {'question': 'Proceed?', 'multi_select': False, 'options': []})

    def test_review_attaches_rendered_views_when_the_planner_takes_images(self):
        # The reviewer must see FORM, not only bounds: a flat plate and a duct share a bounding box.
        import os, tempfile
        tmp = tempfile.mkdtemp()
        for view in ('iso', 'front', 'top', 'right'):
            Path(tmp, f'view-{view}.png').write_bytes(b'\x89PNG\r\n\x1a\n' + view.encode())
        class FakeProject:
            def file(self, head, name):
                path = Path(tmp, name)
                if not path.exists():
                    raise OSError('missing')
                return path

            def read(self):
                return {'revisions': [{'id': 'r0001', 'sha256': {f'view-{v}.png': 'x' for v in ('iso', 'front', 'top', 'right')}}]}
        fake = SimpleNamespace(config={'planner': {'max_images_per_request': 16}},
                               session=SimpleNamespace(project=FakeProject()))
        parts = AgentRunner._revision_view_parts(fake, 'r0001', limit=4)
        images = [p for p in parts if p['type'] == 'image_url']
        self.assertEqual(len(images), 4)
        self.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
        # Gated off when the planner accepts no images, or when there is no saved revision yet.
        fake.config['planner']['max_images_per_request'] = 0
        self.assertEqual(AgentRunner._revision_view_parts(fake, 'r0001'), [])
        fake.config['planner']['max_images_per_request'] = 16
        self.assertEqual(AgentRunner._revision_view_parts(fake, None), [])

    def test_failed_candidate_does_not_modify_saved_ledger(self):
        saved, _, _ = candidate(workspace(), plate())
        original = copy.deepcopy(saved)
        bad = holes(); bad['arguments']['body'] = 'Missing'
        with self.assertRaisesRegex(ValueError, 'Unknown body Missing'):
            candidate(saved, bad)
        self.assertEqual(saved, original)

    def test_pattern_wiring_and_named_parameters_survive_edit(self):
        saved, _, _ = candidate(workspace(), plate())
        saved, design, state = candidate(saved, holes())
        self.assertEqual(len(design['features']), 6)
        self.assertEqual(len(design['features'][-1]['inputs']), 5)
        edited, design, _ = candidate(saved, op('set_parameter', name='length', value=80))
        self.assertEqual(next(p['value'] for p in design['parameters'] if p['name'] == 'length'), 80)
        self.assertEqual(len(edited['operations']), 2)
        self.assertEqual(list(state['bodies']), ['Plate'])

    def test_large_pattern_is_chunked_without_orphans(self):
        saved, _, _ = candidate(workspace(), plate())
        pattern = holes(); pattern['arguments'].update(count_x=8, count_y=4, pitch_x='5', pitch_y='5')
        saved, design, _ = candidate(saved, pattern)
        cuts = [f for f in design['features'] if f['kind'] == 'difference']
        self.assertEqual([len(f['inputs']) for f in cuts], [32, 2])
        self.assertEqual(cuts[1]['inputs'][0], cuts[0]['id'])

    def test_lid_local_patterns_follow_gap_edits(self):
        saved = workspace()
        for item in enclosure_ops():
            saved, _, _ = candidate(saved, item)
        vents = op('cut_pattern', body='Lid', parameters=[], kind='box', dimensions=['3', '24', 'lid_thickness+2'],
                   position=['30', '18', '-1'], rotation=[0, 0, 0], count_x=4, count_y=1,
                   pitch_x='10', pitch_y='0', frame='body')
        saved, design, _ = candidate(saved, vents)
        from server.design import expression
        cutter = next(f for f in design['features'] if f['dimensions'] == ['3', '24', 'lid_thickness+2'])
        values = {p['name']: p['value'] for p in design['parameters']}
        self.assertEqual(expression(cutter['position'][0], values)[0], 130)
        saved, design, _ = candidate(saved, op('set_parameter', name='gap', value=20))
        cutter = next(f for f in design['features'] if f['dimensions'] == ['3', '24', 'lid_thickness+2'])
        values = {p['name']: p['value'] for p in design['parameters']}
        self.assertEqual(expression(cutter['position'][0], values)[0], 140)

    def test_side_window_rejects_floor_and_corner_damage(self):
        saved = workspace()
        for item in enclosure_ops()[:2]:
            saved, _, _ = candidate(saved, item)
        for change in ({'above': '0'}, {'along': '0'}, {'above': '22'}):
            bad = enclosure_ops()[2]; bad['arguments'].update(change)
            with self.assertRaisesRegex(ValueError, 'Side window'):
                candidate(saved, bad)

    def test_replace_operation_replays_dependents_and_preserves_body_identity(self):
        saved = workspace()
        for item in enclosure_ops():
            saved, _, _ = candidate(saved, item)
        corrected = enclosure_ops()[2]; corrected['arguments']['along'] = 'center'
        edited, design, state = candidate(saved, op('replace_operation', index=3, operation=corrected))
        self.assertEqual(len(edited['operations']), 4)
        self.assertEqual(set(state['bodies']), {'Base', 'Lid'})
        port = next(f for f in design['features'] if f['kind'] == 'box' and f['dimensions'][-1] == '8')
        from server.design import expression
        values = {p['name']: p['value'] for p in design['parameters']}
        self.assertEqual(expression(port['position'][0], values)[0], 37)
        corrected['arguments']['body'] = 'AnotherBody'
        with self.assertRaisesRegex(ValueError, 'preserve operation type and body IDs'):
            candidate(saved, op('replace_operation', index=3, operation=corrected))
        self.assertEqual(saved['operations'][2]['arguments']['along'], '20')

    def test_legacy_design_is_preserved_when_adopted(self):
        saved = workspace(block())
        _, design, state = candidate(saved, op('set_parameter', name='length', value=75))
        self.assertEqual(design['features'], block()['features'])
        self.assertIn('Stock', state['bodies'])
        with self.assertRaisesRegex(ValueError, 'axis-aligned rectangular create_body'):
            candidate(saved, op('hollow', body='Stock', wall='2', floor='2', parameters=[]))

    def test_typed_operations_reject_unsafe_or_malformed_inputs(self):
        bad_inputs = [op('execute_python', code='x'), op('inspect', extra='x')]
        for bad in bad_inputs:
            with self.assertRaises(ValueError):
                validate_operation(bad)
        for change in ({'count_x': True}, {'count_x': 100}, {'pitch_x': '__import__("os")'}):
            saved, _, _ = candidate(workspace(), plate())
            bad = holes(); bad['arguments'].update(change)
            with self.assertRaises(ValueError):
                candidate(saved, bad)


class OperationKernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_plate_checkpoint_failure_repair_parameter_edit_and_restore(self):
        with tempfile.TemporaryDirectory(prefix='operation-kernel-') as root:
            project = Project.create(root, 'freecad')
            saved, design, _ = candidate(workspace(), plate())
            stage = await project.prepare(design); atomic_json(stage / 'workspace.json', saved)
            project.commit(stage, None)
            first = project.file('r0001', 'model.FCStd').read_bytes()
            bad = holes(); bad['arguments']['position'][0] = '1000+offset'
            _, broken, _ = candidate(saved, bad)
            with self.assertRaisesRegex(ValueError, 'removed no material'):
                await project.prepare(broken)
            self.assertEqual(project.read()['head'], 'r0001')
            self.assertFalse(list(project.path.glob('.build-*')))
            saved, design, _ = candidate(saved, holes())
            stage = await project.prepare(design); atomic_json(stage / 'workspace.json', saved)
            result = project.commit(stage, 'r0001')
            self.assertAlmostEqual(result['geometry']['volume_mm3'], 60*40*8 - 4*math.pi*2.5**2*8, places=4)
            self.assertEqual(len(result['geometry']['cuts']), 4)
            saved, design, _ = candidate(saved, op('set_parameter', name='length', value=80))
            stage = await project.prepare(design); atomic_json(stage / 'workspace.json', saved)
            result = project.commit(stage, 'r0002')
            self.assertEqual(result['geometry']['bounds_mm'], [80., 40., 8.])
            restored = project.restore('r0002', 'r0003')
            replayed, _ = compile_workspace(restored['workspace'])
            self.assertEqual(replayed, restored['design'])
            self.assertEqual(project.file('r0001', 'model.FCStd').read_bytes(), first)

    async def test_lid_retains_plate_and_side_window_does_not_cut_floor(self):
        with tempfile.TemporaryDirectory(prefix='operation-enclosure-') as root:
            project = Project.create(root, 'freecad')
            saved = workspace()
            for item in enclosure_ops():
                saved, design, _ = candidate(saved, item)
                stage = await project.prepare(design); atomic_json(stage / 'workspace.json', saved)
                result = project.commit(stage, project.read()['head'])
            area = lambda l, w, r: l*w - (4-math.pi)*r*r
            base_volume = area(90, 60, 6)*24 - area(86, 56, 4)*22 - 16*8*2
            lid_volume = area(90, 60, 6)*2 + (area(85.6, 55.6, 3.8) - area(81.6, 51.6, 1.8))*3
            self.assertEqual(result['geometry']['solid_count'], 2)
            self.assertAlmostEqual(result['geometry']['volume_mm3'], base_volume + lid_volume, places=4)
            for measured, expected in zip(result['geometry']['bounds_mm'], [190., 60., 24.]):
                self.assertAlmostEqual(measured, expected, places=6)


class OperationAgentTests(unittest.IsolatedAsyncioTestCase):
    """The modeling agent through native tool calling (mocked model, real candidate/commit path)."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='operation-agent-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
                                  app={'name': 'FreeCAD'}), {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}})
        self.runner.task_text = 'Make a plate with four holes'

    def turns(self, *turns):
        """Each turn: (content, [(tool, args), ...])."""
        replies = iter(turns)
        self.calls = []
        async def chat_tools(endpoint, messages, tools, **kwargs):
            self.calls.append((copy.deepcopy(messages), tools))
            content, calls = next(replies)
            return {'content': content, 'tool_calls': [{'id': f'call{len(self.calls)}_{i}', 'name': t, 'arguments': json.dumps(a)} for i, (t, a) in enumerate(calls)],
                    'reasoning': '', 'finish_reason': 'tool_calls' if calls else 'stop', 'streams': []}
        return chat_tools

    def tool_results(self, index):
        return [json.loads(m['content']) for m in self.calls[index][0] if m['role'] == 'tool']

    async def test_edit_guard_after_build_discards_stage_without_blacklisting_valid_geometry(self):
        chat = self.turns(('', [('create_body', plate()['arguments'])]),
                          ('The document observation is now current.', [('create_body', plate()['arguments'])]),
                          ('Plate saved.', []))
        stages = []
        async def prepare(design):
            stage = stage_fixture(self.project, design)
            stages.append(stage)
            return stage
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), \
                patch('server.agent.present_revision', return_value=None), \
                patch('server.agent.native_edit_blocker', side_effect=[None, 'Observation unavailable', None, None]):
            self.assertTrue(await self.runner._native_model('Make the plate'))
        self.assertEqual(len(stages), 2)
        self.assertTrue(all(not stage.exists() for stage in stages))
        self.assertEqual(self.project.read()['head'], 'r0001')
        self.assertIn('Observation unavailable', self.tool_results(1)[-1]['error'])
        self.assertTrue(self.tool_results(2)[-1]['ok'])
        self.assertFalse(self.runner.paused)

    async def test_pi_question_tool_delivers_the_user_answer(self):
        chat = self.turns(('', [('ask_question', {'question': 'Which connector variant?', 'options': [], 'multi_select': False})]),
                          ('Building the plate.', [('create_body', plate()['arguments'])]), ('Plate saved.', []))
        async def prepare(design): return stage_fixture(self.project, design)
        async def answer():
            while not self.runner._question: await asyncio.sleep(.01)
            self.runner.answer_question(self.runner._question['question_id'], [], 'USB-C')
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            task = asyncio.create_task(answer())
            self.assertTrue(await self.runner._native_model('Make the connector plate'))
            await task
        self.assertEqual(self.tool_results(1)[-1]['answer']['text'], 'USB-C')
        self.assertEqual(self.project.read()['head'], 'r0001')

    async def test_local_error_returns_current_checkpoint_and_only_failed_operation_retries(self):
        good = {'body': 'Plate', 'face': 'zmax', 'u': 'offset', 'v': 'offset', 'diameter': 'diameter', 'depth': 'through', 'count_u': 2, 'count_v': 2,
                'pitch_u': 'length-2*offset', 'pitch_v': 'width-2*offset', 'parameters': params(diameter=5, offset=8)}
        far = {'body': 'Plate', 'kind': 'cylinder', 'dimensions': ['2.5', '10'], 'at': ['1000', '0', '-1'], 'anchor': 'base', 'axis': 'z'}
        chat = self.turns(('Plate first.', [('create_body', plate()['arguments'])]),
                          ('Now the holes.', [('cut', far)]),
                          ('Retrying the holes in place.', [('hole_pattern', good)]),
                          ('Plate with four holes is saved.', []))
        builds = []
        async def prepare(design):
            builds.append(design)
            if len(builds) == 2:
                raise ValueError('Cutter outside stock at X=1000')
            return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('Make the mounting plate'))
        failed = self.tool_results(2)[-1]
        self.assertFalse(failed['ok']); self.assertIn('Cutter outside stock', failed['error']); self.assertEqual(failed['head_unchanged'], 'r0001')
        self.assertEqual(self.project.read()['head'], 'r0002')
        self.assertEqual(len(self.project.public()['workspace']['operations']), 2)
        self.assertEqual(self.runner.completed_steps, 2)
        self.assertEqual(len(builds), 3)
        self.assertEqual(len(self.calls), 4)

    async def test_stream_failure_is_returned_to_native_model_without_a_partial_build(self):
        from server.chat_stream import ModelStreamError
        state = {'n': 0}
        good = self.turns(('Plate.', [('create_body', plate()['arguments'])]), ('Saved.', []))
        async def chat(endpoint, messages, tools, **kwargs):
            state['n'] += 1
            if state['n'] == 1:
                raise ModelStreamError('Model JSON stalled: repeated padding; no operation was executed.', '{"tool":')
            return await good(endpoint, messages, tools, **kwargs)
        builds = []
        async def prepare(design):
            builds.append(design); return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('Make a plate'))
        self.assertEqual(message_text(self.calls[0][0][-1]), 'Make a plate')
        self.assertFalse(any('JSON stalled' in str(m) for m in self.calls[0][0]))
        self.assertEqual(len(builds), 1)
        self.assertEqual(self.project.read()['head'], 'r0001')

    async def test_repeated_invalid_geometry_is_not_executed(self):
        bad = plate()['arguments'] | {'dimensions': ['unknown', '20', '5']}
        chat = self.turns(('', [('create_body', bad)]), ('', [('create_body', bad)]), ('A required dimension is missing.', []))
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare') as prepare:
            self.assertTrue(await self.runner._native_model('Make a block'))
        prepare.assert_not_called()
        self.assertTrue(any('NOT executed again' in e.get('message', '') for e in self.runner.events))

    async def test_completion_review_is_advisory_and_parameter_edits_replay(self):
        chat = self.turns(('Plate.', [('create_body', plate()['arguments'])]),
                          ('Changing the length.', [('set_parameter', {'name': 'length', 'value': 80}), ('review', {})]),
                          ('Revised to 80 mm.', []))
        async def review(*args):
            return {'status': 'revise', 'issues': ['width is 40; the user said nothing about it'], 'summary': 'One doubt'}
        async def prepare(design): return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.runner, '_review_native_requirements', review), \
             patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('Make an 80 mm plate'))
        self.assertEqual(self.project.read()['head'], 'r0002')
        self.assertEqual(self.project.public()['workspace']['parameters']['length'], 80)
        self.assertEqual(self.tool_results(2)[-1]['status'], 'revise')
        self.assertFalse(any(e['t'] == 'tool_error' for e in self.runner.events))
        self.assertTrue(any(e['t'] == 'native_review' for e in self.runner.events))

    async def test_missing_fit_evidence_is_reported_not_a_pause(self):
        chat = self.turns(('Bracket.', [('create_body', plate()['arguments']), ('review', {})]), ('Bracket saved; mounting pitch unknown.', []))
        async def prepare(design): return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), \
             patch('server.agent.present_revision', return_value=None), \
             patch.object(self.runner, '_review_native_requirements', return_value={'status': 'needs_input', 'issues': ['Mounting pitch is unknown'], 'summary': 'Need the device drawing'}):
            self.assertTrue(await self.runner._native_model('Make the device bracket'))
        self.assertFalse(self.runner.paused)
        self.assertEqual(self.project.read()['head'], 'r0001')
        self.assertEqual(self.tool_results(1)[-1]['issues'], ['Mounting pitch is unknown'])

    async def test_stop_during_precommit_check_removes_candidate_not_prior_checkpoint(self):
        arrived = asyncio.Event()
        async def guard(session):
            if list(self.project.path.glob('.build-*')):
                arrived.set()
                await asyncio.Event().wait()
            return None
        chat = self.turns(('Plate.', [('create_body', plate()['arguments'])]))
        async def prepare(design): return stage_fixture(self.project, design)
        async def steer():
            await arrived.wait()
            self.runner.active = True
            self.runner.stop()
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), \
             patch('server.agent.native_edit_blocker', guard), patch('server.agent.present_revision', return_value=None):
            task = asyncio.create_task(self.runner._native_model('Make a plate'))
            await steer()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertIsNone(self.project.read()['head'])
        self.assertFalse(list(self.project.path.glob('.build-*')))
