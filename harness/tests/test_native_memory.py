from pi_fixture import pi_model, messages as pi_messages, entries as pi_entries, message_text
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from server.agent import AgentRunner
from server.attachments import image_part, inspect_image, store_image
from server.projects import Project


class NativeMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Project.create(self.temp.name, 'freecad')
        self.session = SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
            app={'name': 'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        self.config = {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}, 'research': {'enabled': False}}
        self.runner = AgentRunner(self.session, self.config)

    async def until(self, predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.01)

    async def test_stop_resume_delivers_corrections_and_images_after_reopen(self):
        buffer = io.BytesIO()
        Image.new('RGB', (2500, 1000), 'blue').save(buffer, 'PNG')
        attached = store_image(self.project.path / 'attachments', buffer.getvalue())
        reply = AsyncMock(return_value={'content': 'Ready.', 'tool_calls': [], 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Make a Pi case', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        self.runner = AgentRunner(self.session, self.config)
        with pi_model(self.runner, reply):
            self.runner.start('The SD slot belongs on the other narrow end', 'auto', attachments=[attached['id']])
            # A second message before the loop starts must survive independently.
            self.runner.submit_intent('Use this image; do not put it beside the USB ports')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        users = [message_text(m) for m in pi_messages(self.runner) if m['role'] == 'user']
        self.assertEqual(len(users), 3)
        self.assertIn('other narrow end', users[1])
        self.assertIn('beside the USB', users[2])
        restored = AgentRunner(self.session, self.config)
        self.assertEqual(restored.attachments[0]['id'], attached['id'])
        self.assertEqual(len(restored._image_parts(None)[0]), 1)
        self.assertIn(attached['id'], message_text([m for m in pi_messages(restored) if m['role'] == 'user'][1]))
        self.assertEqual(sum(p['type'] == 'image' for m in pi_messages(restored) if isinstance(m['content'], list) for p in m['content']), 1)
        # Crops are given in shown-image pixels (2000 wide here) and cut from the 2500-wide original.
        inspected = inspect_image(self.project.path, attached['id'], [1600, 0, 2000, 400])
        self.assertEqual((inspected['original_width'], inspected['width'], inspected['shown_width']), (2500, 500, 2000))

    async def test_new_task_resets_native_memory_and_old_references(self):
        self.runner.agent_history = [{'role': 'user', 'content': 'Old task'}]
        self.runner.attachments = [{'id': 'old.jpg'}]
        self.runner.design_brief = 'Old brief'
        with patch.object(self.runner, '_run', AsyncMock()):
            self.runner.start('New task', 'auto', new_task=True)
            await self.runner._task
        self.assertEqual(self.runner.agent_history, [])
        self.assertEqual(self.runner.attachments, [])
        self.assertIsNone(self.runner.design_brief)

    async def test_killed_pi_process_keeps_unpersisted_inputs_for_reopen(self):
        started = asyncio.Event()
        async def slow(*args, **kwargs):
            started.set()
            await asyncio.sleep(.3)
            return {'content': 'Ready.', 'finish_reason': 'stop', 'tool_calls': []}
        with pi_model(self.runner, slow):
            self.runner.start('Keep the SD slot on the narrow edge', 'auto')
            await started.wait()
            self.assertEqual(len(self.runner._native_inputs), 1)
            self.runner._pi_bridge.proc.kill()
            await self.until(lambda: not self.runner.active)
        self.runner = AgentRunner(self.session, self.config)
        reply = AsyncMock(return_value={'content': 'Both instructions received.', 'tool_calls': [], 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Also keep the USB ports on the long edge', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        self.assertEqual([message_text(m) for m in pi_messages(self.runner) if m['role'] == 'user'],
                         ['Keep the SD slot on the narrow edge', 'Also keep the USB ports on the long edge'])

    async def test_image_tool_returns_pixels_in_pi_tool_result(self):
        buffer = io.BytesIO()
        Image.new('RGB', (2400, 1200), 'blue').save(buffer, 'PNG')
        attached = store_image(self.project.path / 'attachments', buffer.getvalue())
        reply = AsyncMock(side_effect=[{'content': '', 'tool_calls': [{'id': 'crop', 'name': 'view_image',
            'arguments': __import__('json').dumps({'id': attached['id'], 'crop': [1600, 0, 2000, 400]})}], 'finish_reason': 'tool_calls'},
            {'content': 'The crop is visible.', 'tool_calls': [], 'finish_reason': 'stop'}])
        with pi_model(self.runner, reply):
            self.runner.start('Inspect the corner of this drawing', 'auto', attachments=[attached['id']])
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        result = next(m for m in pi_messages(self.runner) if m['role'] == 'toolResult')
        pixels = next(p for p in result['content'] if p['type'] == 'image')
        import base64
        with Image.open(io.BytesIO(base64.b64decode(pixels['data']))) as image:
            self.assertEqual(image.size, (480, 480))  # 400 shown pixels of a 2400-wide original shown at 2000
        self.assertIn('image_url', str(self.runner.pi_test_requests[-1]['messages']))

    async def test_steering_keeps_completed_tool_results_and_reaches_pi(self):
        replies = iter([{'content': '', 'finish_reason': 'tool_calls', 'tool_calls': [
            {'id': 'a', 'name': 'design_notes', 'arguments': '{"topic":"enclosures"}'}]},
            {'content': 'Correction received.', 'finish_reason': 'stop', 'tool_calls': []}])
        received = []
        async def chat(endpoint, messages, tools, **kwargs):
            received.append(messages)
            if len(received) == 1:
                self.runner.submit_intent('Put the SD opening on the other narrow edge')
                await asyncio.sleep(.1)
            return next(replies)
        with pi_model(self.runner, chat):
            self.runner.start('Build', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        history = pi_messages(self.runner)
        self.assertEqual([m['toolCallId'] for m in history if m['role'] == 'toolResult'], ['a'])
        self.assertTrue(any('other narrow edge' in message_text(m) for m in received[-1] if m['role'] == 'user'))
        self.assertEqual(self.runner._native_inputs, [])

    async def test_blocked_cad_batch_returns_errors_and_delivers_steering_without_pausing_pi(self):
        calls = [{'id': f'wall-{i}', 'name': 'create_body', 'arguments':
                  __import__('json').dumps({'id': f'wall{i}', 'name': f'Wall {i}', 'kind': 'box',
                                           'dimensions': ['10', '2', '5'], 'at': ['0', '0', '0'],
                                           'anchor': 'corner', 'parameters': []})}
                 for i in range(4)]
        reply = AsyncMock(side_effect=[{'finish_reason': 'tool_calls', 'tool_calls': calls},
            {'content': 'The port cutouts are missing. CAD writes are blocked; your saved model is preserved.',
             'finish_reason': 'stop'}])
        guarded = 0
        async def blocker(session):
            nonlocal guarded
            guarded += 1
            if guarded == 1:
                self.runner.submit_intent('I do not see the port cutouts')
                await asyncio.sleep(.1)
            return 'A document changed. Inspect the saved revision; do not retry writes.'
        with pi_model(self.runner, reply), patch('server.agent.native_edit_blocker', blocker), \
                patch.object(self.project, 'prepare', AsyncMock()) as prepare:
            self.runner.start('Build the case', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            self.assertFalse(self.runner.paused)
            self.assertEqual(self.runner._native_inputs, [])
            await self.runner.wait_stopped()
        prepare.assert_not_awaited()
        self.assertEqual(guarded, 4)
        self.assertIsNone(self.project.public()['head'])
        history = pi_messages(self.runner)
        results = [m for m in history if m['role'] == 'toolResult']
        self.assertEqual([m['toolCallId'] for m in results], [c['id'] for c in calls])
        self.assertTrue(all(m['isError'] for m in results))
        self.assertTrue(any('port cutouts' in message_text(m) for m in reply.call_args.args[1] if m['role'] == 'user'))

    async def test_context_and_reasoning_changes_apply_to_next_request_in_the_same_tool_turn(self):
        reply = AsyncMock(side_effect=[{'finish_reason': 'tool_calls', 'tool_calls': [
            {'id': 'a', 'name': 'design_notes', 'arguments': '{"topic":"enclosures"}'}]},
            {'content': 'Continuing with the current model settings.', 'finish_reason': 'stop'}])
        async def execute(*args, **kwargs):
            self.config['planner']['max_model_len'] = 131072
            self.config['agent']['native_reasoning_effort'] = 'off'
            return {'result': {'ok': True}, 'failed': False}
        with pi_model(self.runner, reply), patch.object(self.runner, '_execute_tool', execute):
            self.config['planner'].update(model='qwen3.8-27b', max_model_len=65536)
            self.runner.start('Build', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        first, second = self.runner.pi_test_requests
        self.assertLess(first['max_tokens'], 65536)
        self.assertGreater(second['max_tokens'], 65536)
        self.assertFalse(second['chat_template_kwargs']['enable_thinking'])

    async def test_rejected_live_configuration_ends_the_run_instead_of_stranding_a_tool(self):
        reply = AsyncMock(return_value={'finish_reason': 'tool_calls', 'tool_calls': [
            {'id': 'a', 'name': 'design_notes', 'arguments': '{"topic":"enclosures"}'}]})
        async def execute(*args, **kwargs):
            self.config['planner']['max_images_per_request'] = -1
            return {'result': {'ok': True}, 'failed': False}
        with pi_model(self.runner, reply), patch.object(self.runner, '_execute_tool', execute):
            self.runner.start('Build', 'auto')
            await self.until(lambda: not self.runner.active)
        errors = [e['message'] for e in self.runner.transcript.read() if e['t'] == 'error']
        self.assertTrue(any('could not apply the model settings' in e for e in errors), errors)
        self.assertIsNone(self.runner._pi_bridge)

    async def test_pi_compaction_preserves_history_and_drives_next_request(self):
        self.runner.task_text = 'Existing project'
        self.runner.agent_history = [{'role': 'user' if i % 2 == 0 else 'assistant',
            'content': ('SD slot belongs on the narrow edge. ' if i == 0 else str(i) + ' ') + 'x' * 2000} for i in range(40)]
        reply = AsyncMock(return_value={'content': 'Summary: SD slot belongs on the narrow edge. Keep this orientation.',
                                       'tool_calls': [], 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Check the existing project', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            result = await self.runner._pi_bridge.request('compact')
            self.assertIn('narrow edge', result['summary'])
            self.runner.submit_intent('Continue with that orientation')
            await self.until(lambda: len(self.runner.pi_test_requests) >= 3 and self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        saved = pi_entries(self.runner)
        self.assertTrue(any(e['type'] == 'compaction' for e in saved))
        self.assertTrue(any('narrow edge' in message_text(e['message']) for e in saved if e['type'] == 'message'))
        self.assertIn('Summary: SD slot belongs', str(self.runner.pi_test_requests[-1]['messages']))
        self.assertIsNone(self.runner.context_checkpoint)

    async def test_truncated_response_never_executes_even_complete_looking_calls(self):
        chat = AsyncMock(side_effect=[{'content': '', 'finish_reason': 'length', 'tool_calls': [{'id': 'a', 'name': 'inspect', 'arguments': '{}'}]},
            {'content': 'Done.', 'finish_reason': 'stop', 'tool_calls': []}])
        with pi_model(self.runner, chat), patch.object(self.runner, '_execute_tool', AsyncMock()) as execute:
            await self.runner._native_operations('Test')
        execute.assert_not_awaited()

    async def test_png_mime_and_reference_priority(self):
        png = Path(self.temp.name) / 'render.png'
        Image.new('RGB', (20, 20)).save(png)
        self.assertTrue(image_part(png)['image_url']['url'].startswith('data:image/png;base64,'))
        self.runner.attachments = [{'id': str(i), 'path': str(png), 'label': 'user'} for i in range(4)]
        self.runner.pending_images = [{'id': str(i), 'path': str(png), 'label': 'research'} for i in range(4)]
        parts, labels = self.runner._image_parts(None)
        self.assertEqual(len(parts), 8)
        self.assertTrue(all(label.startswith('user') for label in labels[:4]))

    async def test_legacy_project_recovers_user_turns_that_never_reached_model(self):
        from server.projects import atomic_json
        self.runner.emit({'t': 'user', 'text': 'Original case', 'new_task': True})
        self.runner.emit({'t': 'user', 'text': 'SD belongs on the other narrow edge'})
        atomic_json(self.project.path / 'conversation.json', {'task': 'Original case', 'messages': [], 'history': [],
            'agent_history': [{'role': 'user', 'content': 'Original case'}, {'role': 'assistant', 'content': 'Done'}]})
        restored = AgentRunner(self.session, self.config)
        self.assertEqual([m['content'] for m in restored._native_inputs], ['SD belongs on the other narrow edge'])
        self.assertFalse(restored.active)

    async def test_old_gui_chat_imports_full_transcript_once(self):
        self.runner.task_text = 'Original CAD task'
        self.runner.emit({'t': 'user', 'text': 'The SD slot is on the narrow edge', 'new_task': True})
        for i in range(110):
            self.runner.emit({'t': 'assistant', 'message': f'Older observation {i}'})
        reply = AsyncMock(return_value={'content': 'Orientation retained.', 'finish_reason': 'stop', 'tool_calls': []})
        with pi_model(self.runner, reply):
            self.runner.start('Continue the CAD task', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        imported = pi_messages(self.runner)
        self.assertEqual(message_text(imported[0]), 'The SD slot is on the narrow edge')
        self.assertEqual(sum(message_text(m) == 'Continue the CAD task' for m in imported), 1)

    async def test_model_switch_refreshes_thinking_settings_in_persistent_loop(self):
        observed = []
        async def chat(endpoint, messages, tools, **kwargs):
            observed.append(kwargs['thinking'])
            return {'content': 'Ready.', 'tool_calls': [], 'finish_reason': 'stop'}
        self.config['agent'].update(native_reasoning_effort='low', native_thinking_token_budget=1024)
        with pi_model(self.runner, chat):
            self.runner.start('Initial request', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            self.config['agent'].update(native_reasoning_effort='high', native_thinking_token_budget=None)
            self.runner.submit_intent('Use the new model')
            await self.until(lambda: len(observed) == 2 and self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        self.assertEqual(observed[-1], {'reasoning_effort': 'high', 'thinking_token_budget': None})

    async def test_web_toggle_updates_pi_tools_without_restarting_the_session(self):
        self.runner.research_tool.config['enabled'] = True
        reply = AsyncMock(return_value={'content': 'Ready.', 'tool_calls': [], 'finish_reason': 'stop'})
        with pi_model(self.runner, reply):
            self.runner.start('Start with web search disabled', 'auto')
            await self.until(lambda: self.runner.phase == 'awaiting')
            self.runner.set_web_enabled(True)
            self.runner.submit_intent('Search is now available')
            await self.until(lambda: self.runner.phase == 'awaiting')
            await self.runner.wait_stopped()
        names = lambda body: [t['function']['name'] for t in body['tools']]
        self.assertNotIn('research', names(self.runner.pi_test_requests[0]))
        self.assertIn('research', names(self.runner.pi_test_requests[1]))
