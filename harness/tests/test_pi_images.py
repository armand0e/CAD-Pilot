"""Exercise image retention through real Pi requests, tools, and saved sessions."""
import asyncio
import base64
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pi_fixture import messages as pi_messages
from pi_fixture import pi_model
from PIL import Image
from server.agent import AgentRunner
from server.attachments import image_path, inspect_image, saved_image_ids, store_image
from server.pi_agent import pi_image
from server.projects import Project, atomic_json

ROOT = Path(__file__).resolve().parents[1]


def pixels(messages, kind='image_url'):
    return [part for message in messages if isinstance(message.get('content'), list)
            for part in message['content'] if part['type'] == kind]


def render_revision(project, number):
    revision = f'r{number:04d}'
    folder = project.path / revision
    folder.mkdir()
    hashes = {}
    for index, view in enumerate(('iso', 'top', 'front')):
        path = folder / f'view-{view}.png'
        Image.new('RGB', (80, 60), (number * 10, index * 60, 100)).save(path)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = project.read()
    metadata['revisions'].append({'id': revision, 'sha256': hashes})
    atomic_json(project.path / 'project.json', metadata)
    return revision


class PiImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Project.create(self.temp.name, 'freecad')
        self.session = SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
                                      app={'name': 'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        self.runner = AgentRunner(self.session, {'agent': {'native_operations': True}, 'planner': {},
                                               'policy': {}, 'research': {'enabled': False}})
        self.addAsyncCleanup(self.runner.wait_stopped)

    def references(self, count):
        names = []
        for index in range(count):
            buffer = io.BytesIO()
            Image.new('RGB', (100, 100), (index * 10, 20, 30)).save(buffer, 'PNG')
            names.append(store_image(self.project.path / 'attachments', buffer.getvalue())['id'])
        return names

    async def idle(self):
        async with asyncio.timeout(20):
            while self.runner.phase != 'awaiting':
                await asyncio.sleep(.01)

    async def test_many_revisions_keep_references_current_views_and_reopen_older_crop(self):
        names = self.references(2)
        calls = 0

        async def respond(endpoint, messages, tools, **kwargs):
            nonlocal calls
            self.assertLessEqual(len(pixels(messages)), 16)
            calls += 1
            if calls <= 12:
                name, args = 'define_parameter', {'name': 'wall', 'value': calls}
            elif calls <= 14:
                name, args = 'inspect', {}
            elif calls == 15:
                name, args = 'view_image', {'id': 'cad:r0001:top', 'crop': [10, 10, 50, 40]}
            else:
                return {'content': 'The old top view is available for comparison.', 'finish_reason': 'stop'}
            return {'tool_calls': [{'id': f'call-{calls}', 'name': name, 'arguments': json.dumps(args)}], 'finish_reason': 'tool_calls'}

        original_execute = self.runner._execute_tool

        async def execute(call, ctx, stream):
            if call['name'] != 'define_parameter':
                return await original_execute(call, ctx, stream)
            number = json.loads(call['arguments'])['value']
            ctx['expected_head'] = render_revision(self.project, number)
            return {'result': {'ok': True, 'head': ctx['expected_head']}, 'failed': False}

        with pi_model(self.runner, respond), patch.object(self.runner, '_execute_tool', execute):
            self.runner.start('Use these two references while adjusting the CAD model.', 'auto', attachments=names)
            await self.idle()
            await self.runner.wait_stopped()
        self.assertEqual(calls, 16)
        self.assertFalse([e for e in self.runner.transcript.read() if e['t'] == 'error'])
        final = self.runner.pi_test_requests[-1]['messages']
        self.assertEqual(len(pixels(final)), 6)  # references, latest views, requested old crop
        urls = [p['image_url']['url'] for p in pixels(final)]
        for name in names:
            expected = pi_image(image_path(self.project.path, name))
            self.assertIn(f"data:{expected['mimeType']};base64,{expected['data']}", urls)
        shapes = [Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))).size for url in urls]
        self.assertIn((40, 30), shapes)
        history = pi_messages(self.runner)
        self.assertEqual(len(pixels(history, 'image')), 45)
        self.assertEqual(len([m for m in history if m['role'] == 'toolResult']), 15)
        self.assertIn('cad:r0001:top', str(final))
        # Removing pixels from a request never removes the source artifact.
        with Image.open(image_path(self.project.path, 'cad:r0001:top')) as saved_image:
            self.assertEqual(saved_image.size, (80, 60))

    async def test_oversized_reference_batch_is_explicit_and_requested_image_gets_a_slot(self):
        names = self.references(6)
        calls = 0

        async def respond(endpoint, messages, tools, **kwargs):
            nonlocal calls
            calls += 1
            self.assertLessEqual(len(pixels(messages)), 3)
            self.assertIn('reference images have their pixels omitted', str(messages))
            if calls == 1:
                return {'tool_calls': [{'id': 'old-reference', 'name': 'view_image', 'arguments': json.dumps({'id': names[0], 'crop': []})}],
                        'finish_reason': 'tool_calls'}
            return {'content': 'The earlier reference is now visible.', 'finish_reason': 'stop'}

        with pi_model(self.runner, respond):
            self.runner.config['planner']['max_images_per_request'] = 3
            self.runner.start('Inspect these references in batches.', 'auto', attachments=names)
            await self.idle()
            first_usage = self.runner.image_context
            # A live provider setting takes effect on the next user turn.
            self.runner.config['planner']['max_images_per_request'] = 2
            self.runner.submit_intent('Continue with two images per request.')
            await self.idle()
            self.assertEqual(len(pixels(self.runner.pi_test_requests[-1]['messages'])), 2)
            await self.runner.wait_stopped()
        self.assertGreater(first_usage['reference_omitted'], 0)
        self.assertEqual(first_usage['reference_total'], 6)
        self.assertEqual(len(self.runner.attachments), 6)
        result = next(m for m in pi_messages(self.runner) if m['role'] == 'toolResult')
        image = pixels([result], 'image')[0]
        second_urls = [p['image_url']['url'] for p in pixels(self.runner.pi_test_requests[1]['messages'])]
        self.assertIn(f"data:{image['mimeType']};base64,{image['data']}", second_urls)
        self.assertEqual(len(pixels(pi_messages(self.runner), 'image')), 7)

    async def test_unlabeled_legacy_images_are_archived_and_session_reopens_without_overflow(self):
        names = self.references(18)
        reopen = None
        replies = 0
        self.runner.task_text = 'Existing image conversation'
        self.runner.agent_history = [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f"data:{image['mimeType']};base64,{image['data']}"}}
            for name in names for image in [pi_image(image_path(self.project.path, name))]]}]

        async def respond(endpoint, messages, tools, **kwargs):
            nonlocal replies
            replies += 1
            self.assertLessEqual(len(pixels(messages)), 16)
            if reopen and not any(m.get('tool_call_id') == 'after-compact' for m in messages):
                return {'tool_calls': [{'id': 'after-compact', 'name': 'view_image', 'arguments': json.dumps({'id': reopen, 'crop': []})}],
                        'finish_reason': 'tool_calls'}
            return {'content': 'Image study notes. ' * 2000 if replies <= 2 else 'References are available in batches.', 'finish_reason': 'stop'}

        with pi_model(self.runner, respond):
            self.runner.start('Continue studying the references.', 'auto')
            await self.idle()
            await self.runner.wait_stopped()
        archives = list((self.project.path / 'pi/images').iterdir())
        self.assertEqual(len(archives), 18)
        viewed = inspect_image(self.project.path, 'context:' + archives[0].name)
        self.assertEqual((viewed['width'], viewed['height']), (100, 100))
        self.assertIn('context:' + archives[0].name, saved_image_ids(self.project.path))
        self.runner = AgentRunner(self.session, self.runner.config)
        self.addAsyncCleanup(self.runner.wait_stopped)
        with pi_model(self.runner, respond):
            self.runner.start('Resume the saved conversation.', 'auto')
            await self.idle()
            self.assertEqual(len(pixels(self.runner.pi_test_requests[-1]['messages'])), 16)
            await self.runner._pi_bridge.request('compact')
            reopen = 'context:' + archives[0].name
            self.assertIn(reopen, self.runner._pi_bridge.inspect()['image_archive'])
            self.runner.submit_intent('Reopen an archived reference after compaction.')
            await self.idle()
            self.assertTrue(pixels(self.runner.pi_test_requests[-1]['messages']))
            self.assertTrue(any(m.get('tool_call_id') == 'after-compact' for m in self.runner.pi_test_requests[-1]['messages']))
            await self.runner.wait_stopped()
        self.assertEqual(len(pixels(pi_messages(self.runner), 'image')), 19)

    async def test_bad_attachment_does_not_silently_continue_or_clear_existing_references(self):
        names = self.references(1)
        self.runner._accept_attachments(names)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.runner.start('Use this missing image', 'auto', new_task=True, attachments=['f' * 16 + '.jpg'])
        self.assertEqual([item['id'] for item in self.runner.attachments], names)
        self.assertFalse(self.runner.active)


class ImageProjectionTests(unittest.TestCase):
    def test_projection_preserves_messages_and_supports_legacy_cad_labels(self):
        with tempfile.TemporaryDirectory() as root:
            script = """
                import {imageContext} from './harness/pi/image-context.mjs';
                const cwd=process.argv[1], image={type:'image',mimeType:'image/png',data:'eA=='};
                const messages=[{role:'user',content:[{type:'text',text:'Keep the port on the left.'}]},
                  ...['r0001','r0002','r0002'].map((revision,i)=>({role:'toolResult',toolName:'inspect',toolCallId:String(i),
                    content:[{type:'text',text:'CAD view top, revision '+revision},image]}))];
                const original=JSON.stringify(messages),project=imageContext(cwd);
                const result=project(messages,1);
                if(JSON.stringify(messages)!==original)throw Error('History mutated');
                if(result.messages.length!==messages.length)throw Error('Message lost');
                if(result.usage.attached!==1 || result.usage.omitted!==2)throw Error('Wrong image count');
                if(result.messages.at(-1).content.filter(c=>c.type==='image').length!==1)throw Error('Latest view missing');
                if(!result.messages[1].content.some(c=>c.text?.includes('cad:r0001:top')))throw Error('Old ID lost');
                const batch=[{role:'user',content:[{...image,cadpilotImage:{id:'reference',kind:'reference'}}]},
                  {role:'assistant',content:[{type:'toolCall',id:'a',name:'view_image',arguments:{}},{type:'toolCall',id:'b',name:'view_image',arguments:{}}]},
                  ...['a','b'].map(id=>({role:'toolResult',toolName:'view_image',toolCallId:id,
                    content:[{...image,cadpilotImage:{id,kind:'inspection'}}]}))];
                const viewed=project(batch,2);
                const selected=viewed.messages.flatMap(m=>m.content).filter(c=>c.type==='image').map(c=>c.cadpilotImage.id);
                if(selected.join(',')!=='a,b')throw Error('Requested batch lost to an older reference');
                const tight=project(batch,1);
                if(tight.usage.requested_omitted!==1 || !JSON.stringify(tight.messages).includes('batches of at most 1'))throw Error('Oversized tool batch not disclosed');
            """
            subprocess.run([str(ROOT / '.node/bin/node'), '--input-type=module', '-e', script, root],
                           cwd=ROOT.parent, check=True, capture_output=True, text=True)

    def test_archived_view_validation_and_crop_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project.create(root, 'freecad')
            render_revision(project, 1)
            for name in ('cad:../..:top', 'cad:r0002:top', 'context:../../secret', 'cad:r0001:bottom'):
                with self.assertRaises(ValueError):
                    image_path(project.path, name)
            # A slight overshoot is clamped to the picture; an empty or inverted crop is refused.
            clamped = inspect_image(project.path, 'cad:r0001:top', [0, 0, 81, 61])
            self.assertEqual(clamped['crop'], [0, 0, 80, 60])
            with self.assertRaisesRegex(ValueError, 'Crop must be'):
                inspect_image(project.path, 'cad:r0001:top', [50, 50, 40, 60])
            project.file('r0001', 'view-top.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                image_path(project.path, 'cad:r0001:top')


if __name__ == '__main__':
    unittest.main()
