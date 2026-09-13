"""Run the real Pi SDK against controlled provider endings and output limits."""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pi_fixture import pi_model
from server.agent import AgentRunner
from server.projects import Project


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
