from pi_fixture import pi_model, messages as pi_messages, message_text
"""Model-facing tool contract v2: anchored primitives, face holes, shell, brief, grounding."""
import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner, reasoning_truncated
from server.grounding import caveat, claims_evidence, unsupported_measurements
from server.operations import OPERATION_SCHEMA, OPERATION_SYSTEM, TOOL_DEFINITIONS, candidate, validate_operation, workspace
from server.planning import parse_plan
from server.projects import Project, atomic_json, build_error
from test_projects import stage_fixture


def op(tool, plan='', **arguments):
    return {'plan': plan, 'tool': tool, 'arguments': arguments}


def params(**values):
    return [{'name': k, 'value': v} for k, v in values.items()]


def duct_ops():
    return [op('create_body', id='duct', name='Fan duct', kind='box', dimensions=['L', 'W', 'H'], at=['0', '0', '0'],
               anchor='corner', axis='z', parameters=params(L=120, W=55, H=35, wall=3)),
            op('shell', body='duct', wall='wall', open_faces=['front', 'back'], parameters=[]),
            op('hole_pattern', body='duct', face='top', u='center-offset', v='center-offset', diameter='hole_d',
               depth='through', count_u=2, count_v=2, pitch_u='2*offset', pitch_v='2*offset', parameters=params(hole_d=3, offset=18))]


class ContractTests(unittest.TestCase):
    def test_model_schema_offers_only_v2_tools_with_a_plan(self):
        tools = {v['properties']['tool']['enum'][0] for v in OPERATION_SCHEMA['anyOf']}
        self.assertEqual(tools, {'create_body', 'fuse', 'cut', 'shell', 'hole', 'hole_pattern', 'set_parameter',
                                 'replace_operation', 'inspect', 'view_image', 'brief', 'finish', 'ask', 'ask_question', 'research',
                                 'fillet', 'chamfer', 'mirror', 'polar_pattern', 'place_reference', 'import_reference',
                                 'research_images', 'design_notes', 'recall_facts', 'reply', 'define_parameter',
                                 'define_datum', 'edit_operation', 'delete_operation'})
        for variant in OPERATION_SCHEMA['anyOf']:
            self.assertEqual(variant['required'][:3], ['plan', 'tool', 'arguments'])
            self.assertNotIn('rotation', variant['properties']['arguments']['properties'])
        self.assertLess(len(OPERATION_SYSTEM), 11000)
        for needed in ('anchor', 'axis', 'center+18', 'through', 'brief', 'set_parameter', 'datum'):
            self.assertIn(needed, OPERATION_SYSTEM)
        self.assertEqual({t['function']['name'] for t in TOOL_DEFINITIONS}, tools - {'finish', 'reply'} | {'review'})

    def test_unused_parameters_are_reported_not_rejected(self):
        saved, design, state = candidate(workspace(), duct_ops()[0])
        self.assertEqual(state['unused_parameters'], ['wall'])
        self.assertEqual([p['name'] for p in design['parameters']], ['L', 'W', 'H', 'wall'])

    def test_shell_and_face_holes_expose_wall_bands(self):
        saved = workspace()
        for item in duct_ops():
            saved, design, state = candidate(saved, item)
        body = state['bodies']['duct']
        self.assertEqual(body['open_faces'], ['ymax', 'ymin'])
        self.assertEqual(body['solid_wall_bands_mm'], {'xmin': [0, 3], 'xmax': [117, 120], 'zmin': [0, 3], 'zmax': [32, 35]})
        self.assertEqual(state['unused_parameters'], [])
        self.assertNotIn('plan', saved['operations'][0])
        cutters = [f for f in design['features'] if f['kind'] == 'cylinder']
        self.assertEqual(len(cutters), 4)
        self.assertEqual(cutters[0]['rotation'], [0, 0, 0])

    def test_hole_outside_face_and_hole_into_cavity_report_positions_in_words(self):
        saved = workspace()
        for item in duct_ops()[:2]:
            saved, _, _ = candidate(saved, item)
        with self.assertRaisesRegex(ValueError, r'does not fit on face xmin; the body spans x 0\.\.120, y 0\.\.55, z 0\.\.35'):
            candidate(saved, op('hole', body='duct', face='xmin', u='center+40', v='center', diameter='3', depth='through', parameters=[]))
        with self.assertRaisesRegex(ValueError, 'wall 30 leaves no cavity'):
            candidate(workspace(), duct_ops()[0])[0]
            candidate(candidate(workspace(), duct_ops()[0])[0], op('shell', body='duct', wall='30', open_faces=[], parameters=[]))

    def test_anchors_and_axes_place_primitives_without_rotation_arithmetic(self):
        saved, design, state = candidate(workspace(), op('create_body', id='tube', name='Tube', kind='cylinder', dimensions=['10', '40'],
                                                         at=['0', '0', '0'], anchor='base', axis='y', parameters=params(k=1)))
        self.assertEqual(state['bodies']['tube']['bounds_mm'], {'x': [-10, 10], 'y': [0, 40], 'z': [-10, 10]})
        self.assertEqual(design['features'][0]['rotation'], [-90, 0, 0])
        saved, design, state = candidate(saved, op('fuse', body='tube', kind='box', dimensions=['30', '30', '4'], at=['0', '40', '0'],
                                                   anchor='center', axis='z', parameters=[]))
        self.assertEqual(state['bodies']['tube']['bounds_mm'], {'x': [-15, 15], 'y': [0, 55], 'z': [-10, 10]})
        with self.assertRaisesRegex(ValueError, 'only open its end faces'):
            candidate(saved, op('shell', body='tube', wall='2', open_faces=['xmin'], parameters=[]))
        saved, _, state = candidate(saved, op('shell', body='tube', wall='2', open_faces=['ymax'], parameters=[]))
        self.assertEqual(state['bodies']['tube']['wall_mm'], 2)

    def test_legacy_v1_operations_still_validate_and_reserved_words_are_rejected(self):
        legacy = {'tool': 'create_body', 'arguments': {'id': 'P', 'name': 'Plate', 'kind': 'box', 'dimensions': ['1', '2', '3'],
                  'position': ['0', '0', '0'], 'rotation': [0, 0, 0], 'parameters': []}}
        validate_operation(legacy)
        with self.assertRaisesRegex(ValueError, 'reserved'):
            candidate(workspace(), op('create_body', id='P', name='Plate', kind='box', dimensions=['center', '2', '3'],
                                      at=['0', '0', '0'], anchor='corner', axis='z', parameters=params(center=5)))
        with self.assertRaises(ValueError):
            validate_operation(op('create_body', id='P', name='Plate', kind='box', dimensions=['1', '2', '3'],
                                  at=['0', '0', '0'], anchor='corner', axis='z', rotation=[0, 0, 0], parameters=[]))

    def test_planner_objective_for_model_is_not_squeezed_into_240_characters(self):
        long_objective = 'Airflow duct for the A100: ' + 'user said the screw slots are 36 mm apart, 18 mm from the card centreline; ' * 8
        plan = parse_plan(json.dumps({'decision': 'model', 'objective': long_objective, 'expected_result': 'duct saved',
                                      'message': '', 'wait_seconds': 0}))
        self.assertEqual(plan['objective'], long_objective)
        with self.assertRaises(ValueError):
            parse_plan(json.dumps({'decision': 'act', 'objective': 'x' * 241, 'expected_result': 'y', 'message': '', 'wait_seconds': 0}))

    def test_build_error_keeps_the_kernel_message_not_the_traceback(self):
        log = 'Traceback (most recent call last):\n  File "/worker.py", line 164\n    build()\nValueError: Op005: cutter Op004 removed no material. The cutter sits inside an empty region.\n'
        self.assertEqual(build_error(log), 'Op005: cutter Op004 removed no material. The cutter sits inside an empty region.')


class GroundingTests(unittest.TestCase):
    def test_measurements_need_a_fact_or_a_user_message(self):
        facts = [{'statement': 'The card is 267 mm long', 'quote': 'length of 267 mm', 'source_id': 'web_1'}]
        text = 'I found that the A100 PCIe is a dual-slot card (267 mm long) with a 92 mm blower fan and 36mm hole spacing.'
        self.assertEqual(unsupported_measurements(text, facts, ['they are 36mm apart']), ['92 mm'])
        self.assertTrue(claims_evidence(text))
        self.assertFalse(claims_evidence('I will assume a 92 mm fan unless you say otherwise'))
        self.assertIn('92 mm', caveat(['92 mm']))
        self.assertEqual(caveat([]), '')

    def test_reasoning_truncation_heuristic(self):
        self.assertTrue(reasoning_truncated('x' * 2700 + ' so the orientation would be:\n- X = 120mm (', 1024))
        self.assertFalse(reasoning_truncated('short thought.', 1024))
        self.assertFalse(reasoning_truncated('y' * 3000 + ' Let me fix this by only declaring the three parameters.', 1024))
        self.assertFalse(reasoning_truncated('z' * 5000, None))


class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_duct_with_face_holes_builds_and_reports_volume(self):
        with tempfile.TemporaryDirectory(prefix='tooling-v2-') as root:
            project = Project.create(root, 'freecad')
            saved = workspace()
            for item in duct_ops():
                saved, design, _ = candidate(saved, item)
                stage = await project.prepare(design)
                atomic_json(stage / 'workspace.json', saved)
                result = project.commit(stage, project.read()['head'])
            # through on a shelled body drills this wall only (3 mm), not the far wall too.
            expected = 120 * 55 * 35 - 114 * 55 * 29 - 4 * math.pi * 1.5 ** 2 * 3
            self.assertAlmostEqual(result['geometry']['volume_mm3'], expected, places=3)
            self.assertEqual(result['geometry']['bounds_mm'], [120., 55., 35.])
            self.assertEqual(len(result['geometry']['cuts']), 5)
            # A hole aimed into the cavity is explained in words with the wall bands.
            _, broken, _ = candidate(saved, op('hole', body='duct', face='xmin', u='center', v='center', diameter='3', depth='2', parameters=[]))
            # depth 2 into a 3 mm wall is fine; aim the same hole from a fused block that is not there instead:
            _, broken, _ = candidate(saved, op('cut', body='duct', kind='cylinder', dimensions=['1.5', '20'], at=['60', '27.5', '17.5'],
                                               anchor='center', axis='y', parameters=[]))
            with self.assertRaisesRegex(ValueError, r'inside an empty region.*along x through the cutter center, solid material is at 0\.\.3, 117\.\.120') as failure:
                await project.prepare(broken)
            self.assertNotIn('Traceback', str(failure.exception))
            self.assertEqual(project.read()['head'], 'r0003')

    async def test_tube_axis_y_matches_kernel_bounds(self):
        with tempfile.TemporaryDirectory(prefix='tooling-v2-tube-') as root:
            project = Project.create(root, 'freecad')
            saved, design, state = candidate(workspace(), op('create_body', id='tube', name='Tube', kind='cylinder', dimensions=['r', 'len'],
                                                             at=['5', '10', '15'], anchor='base', axis='y', parameters=params(r=10, len=40)))
            saved, design, state = candidate(saved, op('shell', body='tube', wall='2', open_faces=['ymax'], parameters=[]))
            stage = await project.prepare(design)
            atomic_json(stage / 'workspace.json', saved)
            result = project.commit(stage, None)
            self.assertEqual([round(v, 6) for v in result['geometry']['min_mm']], [-5., 10., 5.])
            self.assertEqual([round(v, 6) for v in result['geometry']['max_mm']], [15., 50., 25.])
            self.assertAlmostEqual(result['geometry']['volume_mm3'], math.pi * 100 * 40 - math.pi * 64 * 38, places=3)


def tool_turns(runner, *turns, truncated_on=()):
    """Mock _chat_tools: each turn is (content, [(tool, args)...]); records messages in runner.calls."""
    replies = iter(turns)
    runner.calls = []
    async def chat_tools(endpoint, messages, tools, **kwargs):
        runner.calls.append(copy.deepcopy(messages))
        runner._last_reasoning_truncated = len(runner.calls) in truncated_on
        content, calls = next(replies)
        return {'content': content, 'tool_calls': [{'id': f'c{len(runner.calls)}_{i}', 'name': t, 'arguments': json.dumps(a)} for i, (t, a) in enumerate(calls)],
                'reasoning': '', 'finish_reason': 'tool_calls' if calls else 'stop', 'streams': []}
    return chat_tools


def tool_results(runner, index):
    return [json.loads(m['content']) for m in runner.calls[index] if m['role'] == 'tool']


def state_of(runner, index):
    content = runner.calls[index][-1]['content']
    return json.loads(content if isinstance(content, str) else content[0]['text'])


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tooling-v2-loop-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}),
                                  {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}})
        self.runner.task_text = 'please make me a fan shroud for my a100'

    async def test_brief_is_recorded_and_transcript_carries_tool_results(self):
        stock = duct_ops()[0]['arguments']
        broken = copy.deepcopy(stock); broken['dimensions'] = ['L', 'W']
        chat = tool_turns(self.runner,
                          ('Plan: duct 120x55x35 (assumed), screw slots 36 mm apart (user).', [('brief', {'text': 'Duct 120x55x35 (assumed), screw slots 36 mm apart (user).'})]),
                          ('Stock first.', [('create_body', broken)]),
                          ('Fixing the dimensions.', [('create_body', stock)]),
                          ('Duct built for the 92 mm fan the datasheet specifies.', []))
        async def prepare(design):
            return stage_fixture(self.project, design)
        with pi_model(self.runner, chat), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('fan shroud for the A100 as discussed'))
        self.assertEqual(self.runner.design_brief, 'Duct 120x55x35 (assumed), screw slots 36 mm apart (user).')
        error = tool_results(self.runner, 2)[-1]
        self.assertFalse(error['ok']); self.assertIn('dimensions', error['error'])
        roles = [m['role'] for m in self.runner.calls[3]]
        self.assertEqual(roles[:4], ['system', 'user', 'assistant', 'tool'])
        final = message_text([m for m in pi_messages(self.runner) if m['role'] == 'assistant'][-1])
        self.assertNotIn('Unverified figures in this reply', final)
        self.assertIn('92 mm', final)
        self.assertEqual(json.loads((self.project.path / 'conversation.json').read_text())['design_brief'], self.runner.design_brief)

    async def test_planner_reply_claiming_found_numbers_is_asked_to_relabel_once(self):
        replies = iter(['{"decision":"ask","objective":"","expected_result":"","message":"I found the A100 has a 92 mm blower fan. Which shroud type?","wait_seconds":0}',
                        '{"decision":"ask","objective":"","expected_result":"","message":"I am assuming a 92 mm fan. Which shroud type?","wait_seconds":0}'])
        calls = []
        async def chat(endpoint, messages, **kwargs):
            calls.append(copy.deepcopy(messages))
            return next(replies)
        with patch.object(self.runner, '_chat', chat):
            await self.runner._plan_intent('')
        self.assertEqual(len(calls), 2)
        self.assertIn('unsupported_measurements', calls[1][-1]['content'])
        self.assertIn('92 mm', calls[1][-1]['content'])
        self.assertEqual(self.runner.plan_details['message'], 'I am assuming a 92 mm fan. Which shroud type?')


class OverlapAndAssumptionTests(unittest.IsolatedAsyncioTestCase):
    def test_assumption_sentences_are_not_flagged_again(self):
        text = 'Plate is 130 mm wide. Assumed values: fan diameter 80 mm (not confirmed). I found the card is 267 mm long.'
        self.assertEqual(unsupported_measurements(text, [], []), ['267 mm'])  # own geometry is not a sourcing claim
        self.assertEqual(unsupported_measurements(text, [], [], claims_only=False), ['130 mm', '267 mm'])
        self.assertEqual(unsupported_measurements('Are you using 30mm, 35mm, or another size? The datasheet says the plate is 130 mm.', [], []), ['130 mm'])

    async def test_overlapping_fan_openings_are_reported_deterministically(self):
        from server.agent import geometry_summary
        with tempfile.TemporaryDirectory(prefix='tooling-v2-overlap-') as root:
            project = Project.create(root, 'freecad')
            saved, design, _ = candidate(workspace(), op('create_body', id='plate', name='Plate', kind='box', dimensions=['130', '100', '3'],
                                                         at=['0', '0', '0'], anchor='center', axis='z', parameters=params(fan_d=80)))
            for x in ('-18', '18'):
                saved, design, _ = candidate(saved, op('cut', body='plate', kind='cylinder', dimensions=['fan_d/2', '5'], at=[x, '0', '0'],
                                                       anchor='center', axis='z', parameters=[]))
            stage = await project.prepare(design)
            atomic_json(stage / 'workspace.json', saved)
            result = project.commit(stage, None)
            cuts = result['geometry']['cuts']
            self.assertNotIn('overlap_fraction', cuts[0])
            self.assertAlmostEqual(cuts[1]['overlap_fraction'], 0.447, places=2)
            summary = geometry_summary(result['geometry'])
            self.assertEqual(len(summary['overlapping_cuts']), 2)
            self.assertIn('re-cuts material that', summary['overlapping_cuts'][0])
            # Through-holes crossing a shell cavity are legitimate and never reported.
            saved = workspace()
            for item in duct_ops():
                saved, design, _ = candidate(saved, item)
            stage = await project.prepare(design)
            report = json.loads((stage / 'geometry.json').read_text())
            self.assertFalse(any('overlap_fraction' in c for c in report['cuts']))
            self.assertNotIn('overlapping_cuts', geometry_summary(report))
            import shutil; shutil.rmtree(stage)


class ContextAndQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tooling-v2-q-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Project.create(self.tmp.name, 'freecad')
        screen = SimpleNamespace(capture_image=lambda: Image.new('RGB', (100, 100), '#444'))
        self.runner = AgentRunner(SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}, screen=screen),
                                  {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}})
        self.runner.task_text = 'make me a raspberry pi 3B case'
        self.runner.active = True

    def test_ledger_keeps_plans_and_state_reports_resolved_geometry_per_operation(self):
        from server.operations import describe_features
        saved = workspace()
        for item in duct_ops():
            saved, design, state = candidate(saved, item | {'plan': 'because ' + item['tool']})
        self.assertEqual([o['plan'] for o in saved['operations']], ['because create_body', 'because shell', 'because hole_pattern'])
        ops = state['operations']
        self.assertEqual(ops[0]['geometry_mm'], ['box x 0..120 y 0..55 z 0..35'])
        self.assertEqual(len(ops[2]['geometry_mm']), 4)
        self.assertIn('cylinder x 40.5..43.5', ops[2]['geometry_mm'][0])
        self.assertEqual(state['feature_operations']['Op002_cavity'], 2)
        self.assertEqual(describe_features('Op004 re-cuts Op002_cavity', state['feature_operations'], saved['operations']),
                         'operation 3 (hole_pattern) re-cuts operation 2 (shell) cavity')
        with self.assertRaisesRegex(ValueError, 'is an opening'):
            candidate(saved, op('hole', body='duct', face='ymin', u='center', v='center', diameter='3', depth='through', parameters=[]))

    async def test_ask_question_waits_for_a_click_without_ending_the_turn(self):
        chat = tool_turns(self.runner,
                          ('Which variant?', [('ask_question', {'question': 'Which Pi variant?', 'options': [{'label': '3B', 'description': ''}, {'label': '3B+', 'description': 'newer'}], 'multi_select': False})]),
                          ('Building for the 3B+.', [('create_body', duct_ops()[0]['arguments'])]),
                          ('Done.', []))
        async def chat_with_click(endpoint, messages, tools, **kwargs):
            result = await chat(endpoint, messages, tools, **kwargs)
            if result['tool_calls'] and result['tool_calls'][0]['name'] == 'ask_question':
                async def click():
                    async with asyncio.timeout(5):
                        while not self.runner._question: await asyncio.sleep(.01)
                    question = self.runner._question
                    self.assertEqual([o['label'] for o in question['options']], ['3B', '3B+'])
                    with self.assertRaises(ValueError):
                        self.runner.answer_question(question['question_id'], ['4B'], '')
                    self.runner.answer_question(question['question_id'], ['3B+'], '')
                asyncio.get_running_loop().create_task(click())
            return result
        async def prepare(design):
            return stage_fixture(self.project, design)
        with pi_model(self.runner, chat_with_click), patch.object(self.project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await self.runner._native_model('pi case'))
        self.assertFalse(self.runner.paused)
        kinds = [e['t'] for e in self.runner.events]
        self.assertIn('question', kinds); self.assertIn('answer', kinds); self.assertNotIn('pause', kinds)
        answered = tool_results(self.runner, 1)[-1]['answer']
        self.assertEqual(answered['selected'], ['3B+'])
        # The model can cite the answer as user evidence without looking the ID up.
        answer_event = next(e for e in self.runner.events if e['t'] == 'answer')
        self.assertEqual(answered['evidence_id'], 'input:' + answer_event['event_id'])
        self.assertEqual(self.runner.dialogue.answered_questions()[-1]['answer'], '3B+')
        self.assertIsNone(self.runner.snapshot()['pending_question'])

    async def test_typed_chat_message_answers_the_open_question(self):
        async def plan(*args):
            self.runner.plan_details = {'decision': 'ask', 'objective': '', 'expected_result': '', 'message': 'Which motor variant?', 'wait_seconds': 0}
            return ''
        async def typed():
            async with asyncio.timeout(2):
                while not self.runner._question:
                    await asyncio.sleep(0.005)
            self.assertEqual(self.runner.snapshot()['pending_question']['question'], 'Which motor variant?')
            # The model is blocked inside its question, so a typed reply is the
            # answer rather than guidance queued behind it.
            self.runner.submit_intent('the 42 mm one')
            self.assertTrue(self.runner._answer_future.done())
            self.assertFalse(any(e['t'] == 'user' and e.get('text') == 'the 42 mm one' for e in self.runner.events))
            self.runner.stop()
        self.runner.config['agent']['native_operations'] = False  # planner path (GUI sessions) still exists
        with patch.object(self.runner, '_plan_intent', plan), patch.object(self.runner, '_ready_image', AsyncMock(return_value=Image.new('RGB', (10, 10)))):
            self.runner.active = False
            self.runner.start('Make a motor mount', 'auto')
            await typed()
            await self.runner.wait_stopped()
        answers = [e for e in self.runner.events if e['t'] == 'answer']
        self.assertEqual(answers[0]['text'], 'the 42 mm one')
        self.assertFalse(any(e['t'] == 'pause' and e.get('paused') for e in self.runner.events))
        self.assertEqual(self.runner.dialogue.answered_questions()[-1], {'question': 'Which motor variant?', 'answer': 'the 42 mm one'})

    async def test_brief_without_any_read_page_gets_the_unread_results_back(self):
        from server.research import source
        self.runner.research['sources'] = [source('https://datasheets.raspberrypi.com/rpi3/drawing.pdf', 'Mechanical drawings, PDF', 'snippet', 'search_result')]
        chat = tool_turns(self.runner, ('Brief.', [('brief', {'text': 'Pi 3B case. All dimensions assumed; no verified source available.'})]), ('Stopping here.', []))
        with pi_model(self.runner, chat):
            self.assertTrue(await self.runner._native_model('pi case'))
        feedback = tool_results(self.runner, 1)[-1]
        self.assertEqual(feedback['research_status']['pages_read'], 0)
        self.assertIn('Mechanical drawings, PDF', feedback['research_status']['unread_search_results'][0])
        self.assertIn('No page has been read yet', feedback['next'])


class ResearchReadHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_sentence_as_query_returns_urls_to_read_instead_of_searching(self):
        from server.research import source
        screen = SimpleNamespace(capture_image=lambda: Image.new('RGB', (10, 10)))
        runner = AgentRunner(SimpleNamespace(screen=screen, app={'name': 'FreeCAD'}), {'agent': {}, 'planner': {}, 'policy': {}, 'research': {'enabled': True}})
        runner.task_text = 'pi case'
        runner.research['sources'] = [source('https://datasheets.raspberrypi.com/rpi3/drawing.pdf', 'Mechanical drawings, PDF', 'snippet', 'search_result')]
        with patch.object(runner.research_tool, 'search', AsyncMock()) as search:
            await runner._research_step('Read the official Raspberry Pi 3B mechanical drawing to extract port positions', 'ports')
        search.assert_not_awaited()
        error = next(e for e in runner.events if e['t'] == 'research_error')
        self.assertIn('exactly one URL', error['message'])
        self.assertIn('datasheets.raspberrypi.com', error['message'])
        self.assertIn('exactly one URL', runner.execution_feedback['error'])


class TimeoutFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_timeout_is_a_correctable_failure_not_the_end_of_the_run(self):
        tmp = tempfile.TemporaryDirectory(prefix='tooling-v2-timeout-'); self.addCleanup(tmp.cleanup)
        project = Project.create(tmp.name, 'freecad')
        runner = AgentRunner(SimpleNamespace(project=project, engine='hybrid', manual_changes=False, app={'name': 'FreeCAD'}),
                             {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}})
        runner.task_text = 'plate'
        good = tool_turns(runner, ('Plate.', [('create_body', duct_ops()[0]['arguments'])]), ('Done.', []))
        state = {'n': 0}
        async def chat(endpoint, messages, tools, **kwargs):
            state['n'] += 1
            if state['n'] == 1:
                runner.calls.append(copy.deepcopy(messages))
                raise RuntimeError('Model request exceeded 180 seconds; no operation from this response was executed.')
            return await good(endpoint, messages, tools, **kwargs)
        async def prepare(design):
            return stage_fixture(project, design)
        with pi_model(runner, chat), patch.object(project, 'prepare', prepare), patch('server.agent.present_revision', return_value=None):
            self.assertTrue(await runner._native_model('plate'))
        self.assertTrue(all('exceeded 180 seconds' not in str(m) for m in runner.calls[1]))
        self.assertEqual([e for e in runner.events if e['t'] == 'tool_error'], [])
        self.assertFalse(any(e['t'] == 'error' for e in runner.events))


class ErrorWordingTests(unittest.TestCase):
    def test_expression_and_parameter_errors_name_the_offender(self):
        from server.design import expression
        with self.assertRaisesRegex(ValueError, r"rejected 'min\(a, 3\)'.*no functions"):
            expression('min(a, 3)', {'a': 1})
        with self.assertRaisesRegex(ValueError, r"unknown name width; declare it"):
            expression('width/2', {'length': 1})
        with self.assertRaisesRegex(ValueError, r"not valid arithmetic: '3 mm'"):
            expression('3 mm', {})
        saved, _, _ = candidate(workspace(), duct_ops()[0])
        with self.assertRaisesRegex(ValueError, r'Parameter wall already exists with value 3.0; reference it by name'):
            candidate(saved, op('shell', body='duct', wall='wall', open_faces=[], parameters=params(wall=2)))
        # Re-declaring with the same value is harmless (it was hoisted to the workspace).
        candidate(saved, op('shell', body='duct', wall='wall', open_faces=[], parameters=params(wall=3)))
