"""CAD tools and UI adapter for the upstream Pi AgentSession.

No inference or context-management loop lives here. The JSON-lines subprocess
owns those concerns; this module handles its lifecycle and executes CAD tools.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

from .attachments import image_part, image_path, saved_image_ids
from .operations import OPERATION_SYSTEM, TOOL_DEFINITIONS, compile_workspace, migrate, workspace

ROOT = Path(__file__).resolve().parents[1]


def pi_image(path, **metadata):
    uri = image_part(path)['image_url']['url']
    header, data = uri.split(',', 1)
    return {'type': 'image', 'mimeType': header[5:].split(';')[0], 'data': data,
            **({'cadpilotImage': metadata} if metadata else {})}


def model_config(runner):
    planner, agent = runner.config['planner'], runner.config['agent']
    return {'baseUrl': planner['base_url'].rstrip('/'), 'id': planner['model'], 'apiKey': planner.get('api_key', ''),
            'contextWindow': planner.get('max_model_len') or agent.get('max_model_len') or 32768,
            'maxImagesPerRequest': planner.get('max_images_per_request') or 16,
            'thinking': agent.get('model_thinking', True) and agent.get('native_reasoning_effort') != 'off',
            'effort': agent.get('native_reasoning_effort', 'medium'),
            'thinkingBudget': agent.get('native_thinking_token_budget'),
            'qwenTemplate': 'qwen' in planner['model'].lower(),
            'timeoutMs': (runner._request_timeout(agent.get('native_request_timeout_s')) or 0) * 1000}


def legacy_messages(runner):
    """Import the existing conversation once, without executing old tool calls."""
    if runner.pi_session or runner._pi_new_session:
        return []
    result = []
    calls = {}
    timestamp = int(time.time() * 1000)
    history = runner.agent_history
    if not history:
        # Older GUI chats only have the UI transcript. Read its whole current
        # task, rather than importing Dialogue's bounded display cache.
        history, answers = [], {}
        for event in runner.transcript.read() if runner.transcript else runner.events:
            kind = event['t']
            if kind == 'user' and event.get('new_task'):
                history, answers = [], {}
            if kind in ('user', 'answer'):
                history.append({'role': 'user', 'content': event.get('text') if kind == 'user' else event.get('summary', event.get('text', ''))})
            elif kind == 'question':
                history.append({'role': 'assistant', 'content': event['question']})
            elif kind == 'assistant' and event.get('presentation') != 'status':
                history.append({'role': 'assistant', 'content': event.get('message', '')})
            elif kind == 'answer_start':
                answers[event['answer_id']] = ''
            elif kind == 'answer_delta':
                answers[event['answer_id']] = answers.get(event['answer_id'], '') + event['text']
            elif kind == 'answer_done':
                text = answers.pop(event['answer_id'], '')
                if text:
                    history.append({'role': 'assistant', 'content': text})
        pending = Counter(i['content'] for i in runner._native_inputs)
        selected = []
        for message in reversed(history):
            if message['role'] == 'user' and pending[message['content']]:
                pending[message['content']] -= 1
            else:
                selected.append(message)
        history = list(reversed(selected))
    for old in history:
        role, content = old.get('role'), old.get('content') or ''
        if isinstance(content, str):
            content = [{'type': 'text', 'text': content}] if content else []
        else:
            parts = []
            for part in content:
                if part.get('type') == 'text':
                    parts.append(part)
                elif part.get('type') == 'image_url' and part.get('image_url', {}).get('url', '').startswith('data:image/'):
                    header, data = part['image_url']['url'].split(',', 1)
                    parts.append({'type': 'image', 'mimeType': header[5:].split(';')[0], 'data': data})
            content = parts
        if role == 'user':
            for name in old.get('attachments', []):
                try:
                    content += [{'type': 'text', 'text': '\nReference image: ' + name}, pi_image(image_path(runner.session.project.path, name))]
                except (OSError, ValueError):
                    continue
            result.append({'role': 'user', 'content': content, 'timestamp': timestamp})
        elif role == 'assistant':
            for call in old.get('tool_calls', []):
                function = call.get('function', {})
                try:
                    arguments = json.loads(function.get('arguments') or '{}')
                except (ValueError, TypeError):
                    arguments = {}
                calls[call['id']] = function.get('name', 'unknown')
                content.append({'type': 'toolCall', 'id': call['id'], 'name': calls[call['id']], 'arguments': arguments})
            result.append({'role': 'assistant', 'content': content, 'timestamp': timestamp,
                           'api': 'openai-completions', 'provider': 'cadpilot', 'model': runner.config['planner'].get('model', 'legacy'),
                           'usage': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0, 'totalTokens': 0,
                                     'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0, 'total': 0}},
                           'stopReason': 'toolUse' if old.get('tool_calls') else 'stop'})
        elif role == 'tool' and old.get('tool_call_id') in calls:
            name = calls.pop(old['tool_call_id'])
            result.append({'role': 'toolResult', 'toolCallId': old['tool_call_id'], 'toolName': name,
                           'content': content, 'isError': False, 'timestamp': timestamp})
    # An interrupted historical batch is a completed cancellation, never work to replay.
    for call_id, name in calls.items():
        result.append({'role': 'toolResult', 'toolCallId': call_id, 'toolName': name, 'isError': True,
                       'content': [{'type': 'text', 'text': 'Interrupted before migration; not executed.'}], 'timestamp': timestamp})
    if runner.context_checkpoint:
        result.append({'role': 'user', 'timestamp': timestamp, 'content': 'Recovered earlier conversation checkpoint:\n' + json.dumps(runner.context_checkpoint)})
    return result


class PiBridge:
    def __init__(self, runner, ctx):
        self.runner, self.ctx = runner, ctx
        self.proc = None
        self.reader = self.stderr = None
        self.pending = {}
        self.tools = {}
        self.write_lock = asyncio.Lock()
        self.activity = asyncio.Event()
        self.idle = True
        self.error = None
        self.turn_error = None
        self.sent = set()
        self.model = None
        self.active_tools = None
        self.streams = {}
        self.message_id = None
        self.message_turn = None
        self.current_turn = runner.turn_id
        self.answer_text = ''
        self.thought_text = ''
        self.tool_inputs = {}

    async def send(self, message):
        if not self.proc or self.proc.returncode is not None:
            raise RuntimeError('Pi runtime is not running')
        async with self.write_lock:
            self.proc.stdin.write((json.dumps(message) + '\n').encode())
            await self.proc.stdin.drain()

    async def request(self, kind, **payload):
        key = payload.pop('id', None) or uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({'type': kind, 'id': key, **payload})
            return await future
        finally:
            self.pending.pop(key, None)

    async def start(self):
        node = os.environ.get('CADPILOT_NODE') or shutil.which('node')
        local = ROOT / '.node/bin/node'
        if not node and local.is_file():
            node = str(local)
        if not node or not (ROOT / 'pi/node_modules/@earendil-works/pi-coding-agent').is_dir():
            raise RuntimeError('Pi is not installed. Run ./harness/pi/setup.sh, or rebuild the CADPilot Docker image.')
        self.proc = await asyncio.create_subprocess_exec(node, str(ROOT / 'pi/runtime.mjs'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024, env={**os.environ, 'PI_OFFLINE': '1', 'PI_SKIP_VERSION_CHECK': '1'})
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())
        self.model = model_config(self.runner)
        definitions = TOOL_DEFINITIONS
        self.active_tools = [t['function']['name'] for t in self.runner._tool_definitions()]
        definitions = [t for t in definitions if t['function']['name'] != 'inspect']
        definitions.append({'type': 'function', 'function': {'name': 'inspect',
            'description': 'Read the current CAD workspace, agreed brief, saved research, image IDs, selection, measurements and rendered views. Use at the start of a task and after resuming.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}})
        session_file = (self.runner.pi_session or {}).get('file')
        if session_file and not Path(session_file).resolve().is_relative_to((self.ctx['project'].path / 'pi/sessions').resolve()):
            session_file = None
        result = await self.request('init', cwd=str(self.ctx['project'].path.resolve()), model=self.model, sessionFile=session_file,
            newSession=getattr(self.runner, '_pi_new_session', False), legacyMessages=legacy_messages(self.runner),
            tools=definitions, activeTools=self.active_tools,
            systemPrompt=OPERATION_SYSTEM + '\nUse inspect to read the existing project before working. Images have IDs; view_image can reopen references and saved CAD views (cad:r0001:top), including after compaction. Older pixels may be omitted from a request; read the image again when visual evidence is needed.\n')
        self.runner._pi_new_session = False
        self.runner.pi_session = {'id': result['sessionId'], 'file': result['sessionFile'], 'runtime': 'pi-coding-agent', 'version': '0.85.1'}
        consumed = set(result.get('consumed', []))
        self.runner._native_inputs = [i for i in self.runner._native_inputs if i.get('id') not in consumed]
        self.runner._save_conversation()

    async def _drain_stderr(self):
        # Drain dependencies' diagnostics without leaking provider credentials to
        # a browser or blocking a full stderr pipe. Keep only a bounded tail.
        self.stderr_tail = b''
        while data := await self.proc.stderr.read(4096):
            self.stderr_tail = (self.stderr_tail + data)[-8192:]

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                message = json.loads(line)
                kind = message['type']
                if kind == 'reply':
                    future = self.pending.get(message['id'])
                    if future and not future.done():
                        if message.get('error'):
                            future.set_exception(RuntimeError(message['error']))
                        else:
                            future.set_result(message.get('result'))
                elif kind == 'input_started':
                    item = next((i for i in self.runner._native_inputs if i.get('id') == message['id']), {})
                    self.current_turn = item.get('turn_id') or self.runner.turn_id
                elif kind == 'input_consumed':
                    self.runner._native_inputs = [i for i in self.runner._native_inputs if i.get('id') != message['id']]
                    self.runner._save_conversation()
                elif kind == 'tool_call':
                    task = asyncio.create_task(self._tool(message), name=message['name'])
                    self.tools[message['id']] = task
                    task.add_done_callback(lambda _, key=message['id']: self.tools.pop(key, None))
                elif kind == 'tool_cancel':
                    if task := self.tools.get(message['id']):
                        task.cancel()
                elif kind == 'event':
                    self._event(message['event'])
                elif kind == 'model_response':
                    self.runner.emit({'t': 'model_response', 'timeline': False,
                                      'turn_id': self.current_turn,
                                      **{k: message.get(k) for k in ('stopReason', 'usage', 'model', 'hasText', 'toolNames')}})
                elif kind == 'usage' and message.get('usage'):
                    usage = message['usage']
                    self.runner.context_usage = {'prompt_tokens': usage.get('tokens'), 'completion_tokens': 0,
                                                  'max_context': usage['contextWindow'], 'runtime': 'pi'}
                    self.runner.emit({'t': 'context_usage', **self.runner.context_usage, 'timeline': False})
                elif kind == 'image_context':
                    self.runner.image_context = {k: v for k, v in message.items() if k != 'type'}
                    self.runner.emit({'t': 'image_context', **self.runner.image_context, 'timeline': False})
                elif kind == 'idle':
                    self.idle = True
                    self.activity.set()
                    self.runner._wake.set()
                elif kind == 'busy':
                    self.idle = False
                    self.turn_error = None
                elif kind == 'runtime_error':
                    self.turn_error = message['message']
                    self.runner.emit({'t': 'error', 'message': message['message']})
        except (ValueError, OSError, asyncio.IncompleteReadError) as error:
            self.error = RuntimeError('Pi bridge failed: ' + str(error))
        finally:
            self.error = self.error or RuntimeError('Pi runtime exited; the saved conversation is preserved.')
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.error)
            self.activity.set()
            self.runner._wake.set()

    def _event(self, event):
        runner = self.runner
        kind = event['type']
        if kind == 'message_end' and event['message']['role'] == 'user':
            self.ctx['tried'], self.ctx['tried_geometry'], self.ctx['reviews'] = set(), set(), {}
        elif kind == 'message_start' and event['message']['role'] == 'assistant':
            self.message_id, self.message_turn = uuid.uuid4().hex, self.current_turn
            self.answer_text = self.thought_text = ''
            self.tool_inputs = {}
            runner.set_phase('modeling')
        elif kind == 'message_update':
            delta = event['assistantMessageEvent']
            update, text = delta['type'], delta.get('delta') or ''
            identity = {'operation_id': 'thinking-' + self.message_id, 'turn_id': self.message_turn}
            if update == 'thinking_start':
                runner.emit({'t': 'thinking_start', **identity, 'label': 'Thinking…'})
            elif update == 'thinking_delta':
                runner.emit({'t': 'thinking_delta', **identity, 'offset': len(self.thought_text), 'text': text})
                self.thought_text += text
            elif update == 'thinking_end':
                runner.emit({'t': 'thinking_done', **identity, 'status': 'completed'})
            elif update in ('text_start', 'text_delta'):
                if not self.answer_text and update == 'text_delta':
                    runner.emit({'t': 'answer_start', 'answer_id': self.message_id, 'turn_id': self.message_turn})
                if text:
                    runner.emit({'t': 'answer_delta', 'answer_id': self.message_id, 'turn_id': self.message_turn,
                                 'offset': len(self.answer_text), 'text': text})
                    self.answer_text += text
            elif update.startswith('toolcall_'):
                index = delta['contentIndex']
                call = delta.get('toolCall') or {}
                entry = self.tool_inputs.setdefault(index, {'id': call.get('id') or f'{self.message_id}-{index}', 'text': '', 'name': call.get('name', '')})
                entry['name'] = call.get('name') or entry['name']
                identity = {'operation_id': entry['id'], 'turn_id': self.message_turn}
                if update == 'toolcall_start':
                    runner.emit({'t': 'tool_input_start', **identity})
                elif update == 'toolcall_delta':
                    runner.emit({'t': 'tool_input_delta', **identity, 'tool': entry['name'], 'offset': len(entry['text']), 'text': text})
                    entry['text'] += text
                elif update == 'toolcall_end':
                    self.streams[call['id']] = identity
                    runner.emit({'t': 'tool_input_done', **identity, 'tool': call['name'], 'arguments': call['arguments'], 'status': 'completed'})
        elif kind == 'message_end' and event['message']['role'] == 'assistant':
            message = event['message']
            failed = message.get('stopReason') in ('error', 'aborted', 'length')
            status = {'error': 'failed', 'aborted': 'cancelled', 'length': 'interrupted'}.get(message.get('stopReason'), 'completed')
            if self.answer_text:
                runner.emit({'t': 'answer_done', 'answer_id': self.message_id, 'turn_id': self.message_turn,
                             'message': message.get('errorMessage', '') if failed else '', 'status': status})
            if self.thought_text:
                runner.emit({'t': 'thinking_done', 'operation_id': 'thinking-' + self.message_id,
                             'turn_id': self.message_turn, 'status': status})
        elif kind == 'tool_execution_end':
            runner.emit({'t': 'tool_settled', **self.streams.get(event['toolCallId'], {'operation_id': event['toolCallId']}),
                         'status': 'failed' if event['isError'] else 'completed'})
        elif kind == 'compaction_start':
            runner.emit({'t': 'note', 'message': 'Pi is compacting the conversation.'})
        elif kind == 'compaction_end':
            runner.emit({'t': 'note', 'message': event.get('errorMessage') or ('Compaction cancelled.' if event.get('aborted') else 'Pi saved the conversation summary; continuing.')})
        elif kind == 'auto_retry_start':
            runner.emit({'t': 'note', 'message': f"Pi is retrying the model request ({event['attempt']}/{event['maxAttempts']})."})

    def inspect(self):
        from .agent import geometry_summary
        runner, ctx = self.runner, self.ctx
        return {'head': ctx['expected_head'], 'workspace': ctx['ledger'], 'state': ctx['state'],
                'geometry': geometry_summary(ctx['geometry'], ctx['state']), 'design_brief': runner.design_brief,
                'research': runner.research, 'library_facts': runner.library_facts,
                'web_search_enabled': runner.web_enabled,
                'selection': runner._selection_context(ctx['geometry'], ctx['state']),
                'references': [{k: v for k, v in image.items() if k != 'path'} for image in runner.attachments + runner.pending_images],
                'image_archive': saved_image_ids(ctx['project'].path),
                'saved_views': [{'revision': revision['id'], 'images': [f"cad:{revision['id']}:{view}" for view in ('iso', 'top', 'front', 'right')
                                if f'view-{view}.png' in revision['sha256']]} for revision in ctx['project'].read()['revisions']]}

    def views(self):
        content = []
        if self.ctx['expected_head']:
            for view in ('iso', 'top', 'front'):
                try:
                    image = pi_image(self.ctx['project'].file(self.ctx['expected_head'], f'view-{view}.png'),
                                     id=f"cad:{self.ctx['expected_head']}:{view}", kind='cad', revision=self.ctx['expected_head'], view=view)
                    content += [{'type': 'text', 'text': f"CAD view {view}, revision {self.ctx['expected_head']}"}, image]
                except (OSError, ValueError):
                    continue
        return content

    async def _tool(self, message):
        runner, ctx = self.runner, self.ctx
        content, failed = [], False
        try:
            while runner.paused and not runner._stop.is_set():
                runner._wake.clear()
                runner.set_phase('paused')
                await runner._interruptible(runner._wake.wait())
            if runner._stop.is_set():
                raise asyncio.CancelledError
            if message['name'] == 'inspect':
                content = [{'type': 'text', 'text': json.dumps(self.inspect())}] + self.views()
            else:
                if message['name'] in ('research', 'research_images') and not runner.web_enabled:
                    raise ValueError('Web search is disabled by the user.')
                previous = {i['id'] for i in runner.pending_images + runner.attachments}
                head = ctx['expected_head']
                outcome = await runner._execute_tool({'id': message['id'], 'name': message['name'], 'arguments': json.dumps(message['arguments'])},
                    ctx, self.streams.get(message['id'], {}))
                failed = outcome.get('failed', False)
                content = [{'type': 'text', 'text': json.dumps(outcome['result'])}]
                if ctx['expected_head'] != head:
                    content += self.views()
                for item in {i['id']: i for i in runner.attachments + runner.pending_images}.values():
                    if item['id'] not in previous or message['name'] == 'view_image':
                        if message['name'] == 'view_image' and item['id'] != outcome['result'].get('id'):
                            continue
                        content += [{'type': 'text', 'text': item['label'] + ' (id: ' + item['id'] + ')'},
                                    pi_image(item['path'], id=item['id'], kind='inspection' if message['name'] == 'view_image' else 'research')]
                runner._save_conversation()
        except asyncio.CancelledError:
            content, failed = [{'type': 'text', 'text': 'Tool cancelled. Inspect the current revision before resuming.'}], True
        except Exception as error:
            content, failed = [{'type': 'text', 'text': str(error)}], True
        try:
            await self.send({'type': 'tool_result', 'id': message['id'], 'content': content, 'isError': failed})
        except (OSError, RuntimeError):
            pass

    async def close(self):
        if self.proc:
            if self.proc.returncode is None:
                try:
                    async with asyncio.timeout(5):
                        await self.request('abort')
                except (TimeoutError, OSError, RuntimeError):
                    pass
                self.proc.stdin.close()
                try:
                    async with asyncio.timeout(5):
                        await self.proc.wait()
                except TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            for task in list(self.tools.values()):
                task.cancel()
            await asyncio.gather(*list(self.tools.values()), return_exceptions=True)
            await asyncio.gather(self.reader, self.stderr, return_exceptions=True)


async def run_pi(runner, intent, *, persistent=False):
    project = runner.session.project
    saved = project.public()
    ledger = migrate(saved.get('workspace') or workspace(saved['design']))
    draft = project.path / 'draft-workspace.json'
    if draft.is_file():
        pending = json.loads(draft.read_text())
        if pending.get('base_head') == saved['head']:
            ledger = migrate(pending['workspace'])
    ctx = {'project': project, 'ledger': ledger, 'expected_head': saved['head'], 'geometry': saved['geometry'],
           'state': compile_workspace(ledger)[1],
           'saved': saved, 'tried': set(), 'tried_geometry': set(), 'reviews': {}, 'changed': False}
    bridge = PiBridge(runner, ctx)
    runner._pi_bridge = bridge
    announced = False
    if not runner._native_inputs:
        runner._native_inputs.append({'role': 'user', 'content': intent})
    try:
        await runner._interruptible(bridge.start())
        while not runner._stop.is_set():
            runner._wake.clear()
            if bridge.error:
                raise bridge.error
            active_tools = [t['function']['name'] for t in runner._tool_definitions()]
            if active_tools != bridge.active_tools:
                await runner._interruptible(bridge.request('tools', active=active_tools))
                bridge.active_tools = active_tools
            if not runner.paused:
                for item in list(runner._native_inputs):
                    item.setdefault('id', uuid.uuid4().hex)
                    item.setdefault('turn_id', runner.turn_id)
                    if item['id'] in bridge.sent:
                        continue
                    current_model = model_config(runner)
                    if current_model != bridge.model:
                        await runner._interruptible(bridge.request('configure', model=current_model))
                        bridge.model = current_model
                    text, images = item['content'], []
                    for name in item.get('attachments', []):
                        images.append(pi_image(image_path(project.path, name), id=name, kind='reference'))
                        text += '\nReference image: ' + name
                    if runner._last_selection:
                        text += '\nSelected CAD geometry: ' + json.dumps(runner._selection_context(ctx['geometry'], ctx['state']))
                    runner._save_conversation()
                    bridge.idle, announced = False, False
                    runner.set_phase('modeling')
                    await runner._interruptible(bridge.request('input', id=item['id'], text=text, images=images))
                    bridge.sent.add(item['id'])
            if bridge.idle and not announced:
                runner.pending_guidance = None
                if not bridge.turn_error:
                    runner.emit({'t': 'done', 'reason': 'Reply sent; the model stays open for more changes.', 'verified': False})
                runner._save_conversation()
                announced = True
                if not persistent:
                    return True
                runner.emit({'t': 'await_intent', 'step': runner.step_number + 1})
            runner.set_phase('paused' if runner.paused else ('awaiting' if bridge.idle else runner.phase))
            await runner._interruptible(runner._wake.wait())
    finally:
        await bridge.close()
        runner._pi_bridge = None
    return False
