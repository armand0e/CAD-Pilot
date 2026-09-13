"""Run the real Pi SDK against controlled provider endings and output limits."""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pi_fixture import pi_model
from server.agent import AgentRunner
from server.projects import Project


class ContextBudgetTransportTests(unittest.TestCase):
    def test_transport_allocation_and_cancellation(self):
        root = Path(__file__).resolve().parents[1]
        node = os.environ.get('CADPILOT_NODE') or shutil.which('node') or str(root / '.node/bin/node')
        result = subprocess.run([node, '--test', str(root / 'tests/context-budget.test.mjs')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PiCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        project = Project.create(self.temp.name, 'freecad')
        session = SimpleNamespace(project=project, engine='hybrid', manual_changes=False,
                                  app={'name': 'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        self.runner = AgentRunner(session, {'agent': {'native_operations': True}, 'planner': {},
                                            'policy': {}, 'research': {'enabled': False}})
        self.addAsyncCleanup(self.runner.wait_stopped)

    async def until_idle(self):
        async with asyncio.timeout(15):
            while self.runner.phase != 'awaiting':
                await asyncio.sleep(.01)

    def records(self, kind):
        return [e for e in self.runner.transcript.read() if e['t'] == kind]

    async def test_no_thinking_budget_uses_pi_remaining_context_not_8k(self):
        reply = AsyncMock(return_value={'content': 'Ready.', 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Create an enclosure', 'auto')
            await self.until_idle()
            await self.runner.wait_stopped()
        request = self.runner.pi_test_requests[0]
        self.assertGreater(request['max_tokens'], 8192)
        self.assertLess(request['max_tokens'], 32768)
        self.assertEqual(self.records('model_response')[0]['stopReason'], 'stop')

    async def test_vllm_output_allocation_after_automatic_compaction_recovers_same_request(self):
        self.runner.task_text = 'Existing enclosure'
        self.runner.agent_history = [{'role': 'user' if i % 2 == 0 else 'assistant',
            'content': ('The SD slot stays on the narrow edge. ' if i == 0 else str(i) + ' ') + 'old notes ' * 1600}
            for i in range(12)]
        calls = []
        async def reply(endpoint, messages, tools, **kwargs):
            body = copy.deepcopy(self.runner.pi_test_requests[-1])
            calls.append(body)
            if len(calls) == 1:
                return {'tool_calls': [{'id': 'read-before-compaction', 'name': 'design_notes',
                                        'arguments': '{"topic":"enclosures"}'}],
                        'usage': {'prompt_tokens': 111508, 'completion_tokens': 29, 'total_tokens': 111537}}
            if len(calls) == 2:
                self.assertFalse(tools, 'This must be Pi\'s automatic summary request')
                return {'content': 'The SD slot stays on the narrow edge. Continue the enclosure; notes were read.'}
            if len(calls) == 3:
                maximum = body['max_tokens']
                lower_bound = 131072 - maximum + 1
                return {'status': 400, 'error_body': {'error': {
                    'message': f"This model's maximum context length is 131072 tokens. However, you requested {maximum} output tokens and your prompt contains at least {lower_bound} input tokens, for a total of at least 131073 tokens. Please reduce the length of the input prompt or the number of requested output tokens. (parameter=input_tokens, value={lower_bound})",
                    'type': 'BadRequestError', 'param': 'input_tokens', 'code': 400}}}
            self.assertNotIn('max_tokens', body)
            return {'content': 'Continuing with the SD slot on the narrow edge.'}
        execute = AsyncMock(return_value={'result': {'notes': 'Detailed geometry result. ' * 1200}, 'failed': False})
        with pi_model(self.runner, reply), patch.object(self.runner, '_execute_tool', execute):
            self.runner.config['planner'].update(model='qwen3.8-27b', max_model_len=131072)
            self.runner.start('Continue the enclosure', 'auto')
            await self.until_idle()
            self.assertEqual(self.records('error'), [])
            self.assertEqual(len(self.records('done')), 1)
            self.assertEqual(len(calls), 4)
            retry = copy.deepcopy(calls[2]); retry.pop('max_tokens')
            self.assertEqual(calls[3], retry, 'Retry only the rejected allocation; preserve messages, tools and thinking settings')
            self.assertEqual(execute.await_count, 1, 'A completed tool must not run again during recovery')
            self.assertTrue(any('compacting' in e['message'] for e in self.records('note')))
            self.runner.submit_intent('Keep that orientation')
            await self.until_idle()
            self.assertNotIn('max_tokens', calls[-1])
            self.assertNotIn('thinking_token_budget', calls[-1])
            self.assertTrue(calls[-1]['chat_template_kwargs']['enable_thinking'])
            recorded = json.loads((self.runner.session.project.path / 'last-model-request.json').read_text())
            self.assertEqual(recorded, calls[-1], 'Diagnostics must describe the request actually transmitted')
            await self.runner.wait_stopped()

    async def test_exhausted_length_is_an_error_without_success_and_next_turn_works(self):
        reply = AsyncMock(side_effect=[
            {'reasoning': 'Still planning the enclosure.', 'finish_reason': 'length',
             'usage': {'prompt_tokens': 1000, 'completion_tokens': 32768, 'total_tokens': 33768}},
            {'content': 'Continuing with your saved instructions.', 'finish_reason': 'stop'}])
        with pi_model(self.runner, reply):
            self.runner.start('Create an enclosure', 'auto')
            await self.until_idle()
            self.assertIn('output limit', self.records('error')[-1]['message'])
            self.assertEqual(self.records('done'), [])
            self.assertEqual(self.records('model_response')[-1]['stopReason'], 'length')
            self.runner.submit_intent('Continue')
            await self.until_idle()
            self.assertEqual(len(self.records('done')), 1)
            await self.runner.wait_stopped()

    async def test_pi_recovers_context_clamped_length_before_reporting_completion(self):
        reply = AsyncMock(side_effect=[
            {'content': 'Previous saved design notes. ' * 1600, 'finish_reason': 'stop',
             'usage': {'prompt_tokens': 1000, 'completion_tokens': 11000, 'total_tokens': 12000}},
            {'reasoning': 'Not finished.', 'finish_reason': 'length',
             'usage': {'prompt_tokens': 26000, 'completion_tokens': 100, 'total_tokens': 26100}},
            {'content': '## Goal\nCreate an enclosure.\n## Progress\nNo CAD changes yet.', 'finish_reason': 'stop'},
            {'content': 'Ready to continue the enclosure.', 'finish_reason': 'stop'}])
        with pi_model(self.runner, reply):
            self.runner.start('Prepare the enclosure plan', 'auto')
            await self.until_idle()
            self.runner.submit_intent('Create the enclosure from that plan')
            await self.until_idle()
            self.assertEqual(self.records('error'), [])
            self.assertEqual(len(self.records('done')), 2)
            self.assertTrue(any('compacting' in e['message'] for e in self.records('note')),
                            {'notes': self.records('note'), 'calls': reply.await_count, 'endings': self.records('model_response')})
            self.assertEqual(reply.await_count, 4)
            await self.runner.wait_stopped()

    async def test_length_on_first_turn_without_compactable_history_is_not_success(self):
        reply = AsyncMock(return_value={'reasoning': 'Not finished.', 'finish_reason': 'length',
            'usage': {'prompt_tokens': 26000, 'completion_tokens': 100, 'total_tokens': 26100}})
        with pi_model(self.runner, reply):
            self.runner.start('Create an enclosure', 'auto')
            await self.until_idle()
            self.assertIn('output limit', self.records('error')[-1]['message'])
            self.assertEqual(self.records('done'), [])
            await self.runner.wait_stopped()

    async def test_provider_error_is_not_followed_by_a_success_event(self):
        reply = AsyncMock(return_value={'status': 400, 'error': 'Provider rejected the request'})
        with pi_model(self.runner, reply):
            self.runner.start('Create an enclosure', 'auto')
            await self.until_idle()
            self.assertIn('Provider rejected', self.records('error')[-1]['message'])
            self.assertEqual(self.records('done'), [])
            await self.runner.wait_stopped()

    async def test_thinking_only_stop_is_reported_as_missing_answer(self):
        reply = AsyncMock(return_value={'reasoning': 'I should build it.', 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Create an enclosure', 'auto')
            await self.until_idle()
            self.assertIn('without an answer', self.records('error')[-1]['message'])
            self.assertEqual(self.records('done'), [])
            await self.runner.wait_stopped()

    async def test_qwen_effort_and_off_reach_template_arguments_between_turns(self):
        reply = AsyncMock(return_value={'content': 'Ready.', 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.config['planner']['model'] = 'qwen3.8-27b'
            for index, effort in enumerate(('low', 'medium', 'xhigh', 'off', 'low')):
                self.runner.config['agent']['native_reasoning_effort'] = effort
                if index == 0:
                    self.runner.start('Use low reasoning', 'auto')
                else:
                    self.runner.submit_intent('Use ' + effort + ' reasoning')
                await self.until_idle()
                request = self.runner.pi_test_requests[-1]
                template = request['chat_template_kwargs']
                self.assertEqual(template['enable_thinking'], effort != 'off')
                self.assertTrue(template['preserve_thinking'])
                if effort == 'off':
                    self.assertNotIn('reasoning_effort', template)
                else:
                    self.assertEqual(template['reasoning_effort'], effort)
                self.assertNotIn('reasoning_effort', request)
                self.assertNotIn('thinking_token_budget', request)
            await self.runner.wait_stopped()
