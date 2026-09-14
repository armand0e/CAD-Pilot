"""Real parent/child Pi sessions with controlled research evidence."""
import asyncio
import io
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

from pi_fixture import pi_model, messages as pi_messages, message_text
from server.agent import AgentRunner
from server.dimension_research import Investigation, investigate, excerpt, parts_of, quoted
from server.research_reader import rank_links
from server.projects import Project
from server.research import ResearchTool
from server.attachments import image_path, image_provenance
from server.research_progress import ResearchProgress, research_snapshot


def png_bytes(size=(320, 240)):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'white').save(buffer, 'PNG')
    return buffer.getvalue()


def tool_text(message):
    content = message['content']
    return content if isinstance(content, str) else ''.join(c.get('text', '') for c in content if c.get('type') == 'text')


def last_tool_result(messages):
    return json.loads(tool_text([m for m in messages if m['role'] == 'tool'][-1]).split('\n')[0])


def first_tool_result(messages):
    return json.loads(tool_text([m for m in messages if m['role'] == 'tool'][0]).split('\n')[0])


class DimensionResearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.project = Project.create(self.temp.name, 'freecad')
        session = SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
                                  app={'name': 'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        self.runner = AgentRunner(session, {'agent': {'native_operations': True}, 'planner': {}, 'policy': {}, 'research': {'enabled': True}})
        self.addAsyncCleanup(self.runner.wait_stopped)
        self.url = 'https://manufacturer.example/drawing'
        # Part 1 holds the length; only part 2 holds the thickness, so a quote from
        # a later part must validate against the whole document, not the last view.
        self.text = ('Fixture board rev A mechanical data. Fixture board length is 65 mm. PRIVATE_FULL_INVESTIGATION ' +
                     'general description of the product family and ordering codes. ' * 160 +
                     'Board thickness is 1.6 mm (PCB nominal). Mounting holes Ø2.7 mm at 3.5 mm from each edge. ' +
                     'warranty and contact information. ' * 120)
        self.page = {'kind': 'page', 'url': self.url, 'requested_url': self.url, 'title': 'Official fixture drawing', 'text': self.text,
                     'links': [{'title': 'Download mechanical drawing (PDF)', 'url': 'https://manufacturer.example/drawing.pdf'},
                               {'title': 'Careers', 'url': 'https://manufacturer.example/careers'}], 'headings': []}
        self.lead = {'id': 'web_lead', 'url': self.url, 'title': 'Official fixture drawing', 'kind': 'search_result',
                     'snippet': 'Fixture board mechanical drawing ' + 'and much more text ' * 80}

    def search_fixture(self, barrier=None):
        async def search(tool, query):
            if barrier is not None:
                if query.endswith('B'):
                    barrier.set()
                else:
                    # Passes only if Pi executes the sibling search concurrently.
                    await asyncio.wait_for(barrier.wait(), 5)
            rows = [dict(self.lead, id=f'web_lead{i}', url=f'{self.url}?v={i}') for i in range(15)]
            rows[0] = dict(self.lead)
            return {'operation': 'search', 'query': query, 'sources': rows}
        return search

    async def test_parent_delegates_and_child_reports_compactly_from_paged_documents(self):
        parent_calls = child_calls = 0; child_tools = set(); parent_tools = set(); child_payloads = []
        barrier = asyncio.Event()
        async def reply(endpoint, messages, tools, **kwargs):
            nonlocal parent_calls, child_calls
            is_child = 'dimension researcher' in str(messages[0])
            if is_child:
                child_tools.update(t['function']['name'] for t in tools)
                child_calls += 1
                if child_calls == 1:
                    self.assertIn('Part: Fixture board rev A', str(messages[1]))
                    self.assertIn('Budget: up to', str(messages[1]))
                    calls = [('web_search', {'query': 'Fixture board rev A drawing A'}), ('web_search', {'query': 'Fixture board rev A drawing B'})]
                elif child_calls == 2:
                    result = last_tool_result(messages)
                    child_payloads.append(json.dumps(result))
                    self.assertEqual(len(result['results']), 8)
                    self.assertTrue(all(len(r['snippet']) <= 280 for r in result['results']))
                    calls = [('web_read', {'url': self.url, 'focus': 'board thickness'})]
                elif child_calls == 3:
                    result = last_tool_result(messages)
                    self.assertIn('thickness is 1.6 mm', result['text'])
                    self.assertGreater(result['parts'], 1)
                    self.assertEqual([l['url'] for l in result['links']], ['https://manufacturer.example/drawing.pdf'])
                    calls = [('web_read', {'url': self.url, 'part': 1})]
                elif child_calls == 4:
                    result = last_tool_result(messages)
                    self.assertEqual(result['part'], 1)
                    self.assertIn('length is 65 mm', result['text'])
                    self.assertNotIn('thickness is 1.6', result['text'])
                    calls = [('submit_research', {'part_identity': 'Fixture board rev A', 'summary': 'Length and thickness documented.',
                        'dimensions': [{'name': 'board length', 'value': 65, 'unit': 'mm', 'datum': 'X extent', 'source_id': result['id'], 'quote': 'board length is 65 mm'},
                                       {'name': 'board thickness', 'value': 1.6, 'unit': 'mm', 'datum': 'Z extent', 'source_id': result['id'], 'quote': 'Board thickness is 1.6 mm'}],
                        'assumptions': [], 'unknowns': []})]
                else:
                    self.fail('The child must stop after a complete report; no finishing turn is needed')
            else:
                parent_tools.update(t['function']['name'] for t in tools)
                parent_calls += 1
                if parent_calls == 1:
                    calls = [('research_dimensions', {'part_identity': 'Fixture board rev A', 'dimensions': ['board length', 'board thickness'], 'context': 'Do not mix revisions.'})]
                else:
                    text = tool_text([m for m in messages if m['role'] == 'tool'][-1])
                    self.assertLess(len(text), 4000)
                    self.assertNotIn('PRIVATE_FULL_INVESTIGATION', text)
                    self.assertEqual(text.count('board length is 65 mm'), 1)
                    return {'content': 'Both dimensions are documented.'}
            return {'content': '', 'tool_calls': [{'id': f'{"c" if is_child else "p"}{child_calls if is_child else parent_calls}-{i}', 'name': n, 'arguments': json.dumps(a)} for i, (n, a) in enumerate(calls)]}
        async def fetch(tool, url):
            self.assertEqual(url, self.url)
            return dict(self.page)
        with patch.object(ResearchTool, 'search', self.search_fixture(barrier)), patch.object(ResearchTool, 'fetch', fetch), pi_model(self.runner, reply):
            self.runner.start('Model the fixture board case', 'auto')
            async with asyncio.timeout(40):
                while self.runner.phase != 'awaiting': await asyncio.sleep(.02)
            await self.runner.wait_stopped()
        self.assertEqual((parent_calls, child_calls), (2, 4))
        self.assertEqual(child_tools, {'web_search', 'web_read', 'view_image', 'web_images', 'submit_research'})
        self.assertIn('ask_question', parent_tools); self.assertNotIn('ask', parent_tools); self.assertIn('research_dimensions', parent_tools)
        self.assertLess(len(child_payloads[0]), 4000)
        result = json.loads(message_text([m for m in pi_messages(self.runner) if m.get('role') == 'toolResult'][-1]))
        self.assertEqual(result['status'], 'reported')
        self.assertEqual([d['name'] for d in result['dimensions']], ['board length', 'board thickness'])
        self.assertEqual(result['documented_dimensions'], 2)
        self.assertEqual(len(result['sources']), 1)
        self.assertNotIn('notes', result)
        tasks = list((self.project.path / 'research-tasks').iterdir()); self.assertEqual(len(tasks), 1)
        report = json.loads((tasks[0] / 'dimension-report.json').read_text())
        self.assertEqual(report['status'], 'reported')
        self.assertIn('PRIVATE_FULL_INVESTIGATION', ''.join(p.read_text() for p in (tasks[0] / 'pi/sessions').glob('*.jsonl')))
        self.assertEqual(len(self.runner.research['notes']['facts']), 2)
        self.assertEqual(self.runner.research['sources'][-1]['investigation'], tasks[0].name)
        progress = [e for e in self.runner.transcript.read() if e['t'] == 'research_agent']
        self.assertEqual(progress[0]['status'], 'running'); self.assertEqual(progress[0]['step']['activity'], 'Started')
        titles = [e['step']['activity'] for e in progress if e.get('step')]
        self.assertTrue(any(t.startswith('Searched') for t in titles)); self.assertTrue(any(t.startswith('Read Official') for t in titles))
        self.assertTrue(any(t.startswith('Submitted findings: 2 documented') for t in titles))
        self.assertFalse(any(e['activity'] == 'Thinking about the next step' for e in progress), 'live activity is not persisted')
        self.assertTrue(all('sources' in e for e in progress))
        self.assertEqual(progress[-1]['status'], 'completed')
        self.assertEqual([f['name'] for f in progress[-1]['findings']], ['board length', 'board thickness'])
        self.assertTrue(any(s.get('cited') for s in progress[-1]['sources']))
        self.assertNotIn('PRIVATE_FULL_INVESTIGATION', str(progress))
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'], 'completed')
        from fastapi.testclient import TestClient
        from server.app import app
        with patch('server.app._project', return_value=self.project):
            response = TestClient(app, base_url='http://localhost').get(f'/api/projects/{self.project.id}/investigations/{tasks[0].name}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['report']['documented_dimensions'], 2)

    async def test_pdf_pages_render_on_demand_and_one_correction_round_fixes_unsupported_rows(self):
        calls = 0
        pdf_url = 'https://manufacturer.example/drawing.pdf'
        document = {'kind': 'pdf', 'url': pdf_url, 'requested_url': pdf_url, 'title': 'drawing.pdf', 'links': [], 'headings': [],
                    'text': 'Page one: ordering information.\fPage two: BOARD OUTLINE 65 x 30 mm.\fPage three: revision history.', 'pdf': b'%PDF-fake', 'pages': 3}
        rendered = []
        async def pages(content, pages=2, dpi=110, *, first=1, last=None):
            rendered.append((first, last))
            return [{'page': p, 'jpeg': png_bytes((300 + p, 200))} for p in range(first, (last or first) + 1)]
        async def fetch(tool, url):
            return dict(document)
        async def reply(endpoint, messages, tools, **kwargs):
            nonlocal calls
            calls += 1
            try:
                return await steps(messages)
            except Exception:
                import traceback; traceback.print_exc()
                raise
        async def steps(messages):
            if calls == 1:
                name, args = 'web_read', {'url': pdf_url, 'pages': [2, 3]}
            elif calls == 2:
                result = last_tool_result(messages)
                self.assertEqual(result['rendered_pages'], [2, 3]); self.assertEqual([i['page'] for i in result['images']], [2, 3])
                self.assertIn('BOARD OUTLINE', result['text'])
                name, args = 'view_image', {'id': result['images'][0]['id'], 'crop': [0, 0, 100, 80]}
            elif calls == 3:
                crop = last_tool_result(messages)
                source = crop['source_id']
                name, args = 'submit_research', {'part_identity': 'fixture', 'summary': 'Outline read from the drawing.',
                    'dimensions': [{'name': 'board width', 'value': 30, 'unit': 'mm', 'datum': 'outline', 'source_id': first_tool_result(messages)['id'], 'quote': '30', 'image_id': crop['id']},
                                   {'name': 'board length', 'value': 65, 'unit': 'mm', 'datum': 'outline', 'source_id': first_tool_result(messages)['id'], 'quote': 'length is 999 mm'}],
                    'assumptions': ['Drawing scale assumed 1:1'], 'unknowns': []}
            elif calls == 4:
                outcome = last_tool_result(messages)
                self.assertEqual(outcome['accepted'], 1); self.assertEqual(outcome['drawing_readings'], 1)
                self.assertEqual(outcome['rejected'][0]['name'], 'board length'); self.assertIn('once more', outcome['next'])
                source = first_tool_result(messages)['id']
                name, args = 'submit_research', {'part_identity': 'fixture', 'summary': 'Outline documented.',
                    'dimensions': [{'name': 'board length', 'value': 65, 'unit': 'mm', 'datum': 'outline', 'source_id': source, 'quote': 'board outline 65 x 30 mm'}],
                    'assumptions': [], 'unknowns': ['Hole positions are not on these pages.']}
            else:
                self.fail('The report is final after the correction round')
            return {'content': '', 'tool_calls': [{'id': f'child-{calls}', 'name': name, 'arguments': json.dumps(args)}]}
        with patch.object(ResearchTool, 'fetch', fetch), patch('server.research_reader.pdf_page_images', pages), pi_model(self.runner, reply):
            async with asyncio.timeout(40):
                result = await investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['outline']})
        self.assertEqual(calls, 4)
        self.assertEqual(rendered, [(2, 3)])
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['documented_dimensions'], 1); self.assertEqual(result['visual_dimensions'], 1)
        self.assertEqual(result['unknowns'], ['Hole positions are not on these pages.'])
        drawing = next(d for d in result['dimensions'] if d['evidence'] == 'drawing_image')
        self.assertEqual(drawing['page'], 2)
        self.assertEqual(image_provenance(self.project.path, drawing['image_id'])['crops'], [[0, 0, 100, 80]])
        self.assertTrue(image_path(self.project.path, drawing['image_id']).exists())
        self.assertTrue(any('Unverified drawing reading' in s for s in self.runner.research['notes']['assumptions']))
        self.assertEqual(len(self.runner.research['notes']['facts']), 1)
        progress = [e for e in self.runner.transcript.read() if e['t'] == 'research_agent']
        titles = [e['step']['activity'] for e in progress if e.get('step')]
        self.assertIn('Rendered drawing pages 2-3', titles); self.assertIn('Cropped drawing page 2', titles)
        self.assertEqual(progress[-1]['status'], 'incomplete')
        self.assertEqual(progress[-1]['findings'][0]['evidence'], 'drawing_image')

    async def test_call_budget_ends_a_wandering_investigation_with_an_honest_partial_result(self):
        self.runner.config['research']['max_investigation_calls'] = 4
        calls = 0
        async def reply(endpoint, messages, tools, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 5:
                self.assertIn('budget is used up', str(messages[-1]))
            return {'content': '', 'tool_calls': [{'id': f'c{calls}', 'name': 'web_search', 'arguments': json.dumps({'query': f'query {calls}'})}]}
        with patch.object(ResearchTool, 'search', self.search_fixture()), pi_model(self.runner, reply):
            async with asyncio.timeout(40):
                result = await investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length', 'width']})
        self.assertLessEqual(calls, 8)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['unknowns'][:2], ['length', 'width'])
        self.assertEqual(result['calls'], 4)
        self.assertIn('found no usable documentation', result['summary'])
        self.assertEqual(result['sources'], [])
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'], 'incomplete')
        steps = [e['step']['activity'] for e in self.runner.transcript.read() if e['t'] == 'research_agent' and e.get('step')]
        self.assertIn('Asked the researcher to report', steps)

    async def test_budget_reminders_deadline_and_text_helpers(self):
        child = Project.create(self.project.path / 'research-tasks', 'research')
        investigation = Investigation(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']}, child)
        investigation.calls = investigation.max_calls - 2
        investigation.child = SimpleNamespace(_native_inputs=[], turn_id=None, _wake=asyncio.Event())
        with patch.object(ResearchTool, 'search', self.search_fixture()):
            content, failed = await investigation.execute('web_search', {'query': 'fixture'})
        self.assertFalse(failed); self.assertIn('tool call(s)', json.loads(content[0]['text'])['budget'])
        # The steer reaches the child once, several turns before the budget is spent.
        self.assertEqual(len(investigation.child._native_inputs), 1)
        self.assertIn('submit_research now', investigation.child._native_inputs[0]['content'])
        self.assertTrue(investigation.child._wake.is_set())
        with patch.object(ResearchTool, 'search', self.search_fixture()):
            await investigation.execute('web_search', {'query': 'fixture again'})
        self.assertEqual(len(investigation.child._native_inputs), 1)
        investigation.deadline = time.time() - 1
        content, failed = await investigation.execute('web_search', {'query': 'fixture'})
        self.assertTrue(failed); self.assertIn('budget is used up', content[0]['text'])
        content, failed = await investigation.execute('cad_build', {})
        self.assertTrue(failed)
        with self.assertRaisesRegex(ValueError, 'not a saved image'):
            investigation._dimension({'name': 'w', 'value': 1, 'unit': 'mm', 'datum': 'd', 'source_id': 'web_unknown', 'quote': '1', 'image_id': 'missing.jpg'})
        with self.assertRaisesRegex(ValueError, 'search leads'):
            investigation._dimension({'name': 'w', 'value': 1, 'unit': 'mm', 'datum': 'd', 'source_id': 'web_lead', 'quote': 'Fixture board'})
        # Hub pages carry many links; the ones matching the focus and the part come first, drawings ranked up.
        source = {'links': [{'title': 'Careers', 'url': 'https://m.example/jobs'}, {'title': 'Mechanical drawings, PDF', 'url': 'https://m.example/RP-5', 'context': 'Raspberry Pi 5'},
                            {'title': 'Mechanical drawings, PDF', 'url': 'https://m.example/RP-Z', 'context': 'Raspberry Pi Zero'}, {'title': 'Zero product page', 'url': 'https://m.example/zero'}]}
        ranked = rank_links(source['links'], 'mechanical drawing PDF', 'Raspberry Pi Zero v1.1')
        self.assertEqual([l['url'] for l in ranked][:2], ['https://m.example/RP-Z', 'https://m.example/RP-5'])
        self.assertEqual(ranked[0]['section'], 'Raspberry Pi Zero')
        self.assertNotIn('https://m.example/jobs', [l['url'] for l in ranked])
        self.assertTrue(quoted('Board thickness is 1.6mm', 'the  board Thickness is 1.6 mm (PCB)'))
        self.assertTrue(quoted('holes Ø2.7 mm', 'Mounting holes ⌀2.7 mm at')); self.assertFalse(quoted('1.6', 'thickness 1.6 mm'))
        self.assertFalse(quoted('length is 999 mm', 'length is 65 mm'))
        parts = parts_of('a' * 20000)
        self.assertEqual(len(parts), 3); self.assertEqual(parts[1][:300], parts[0][-300:])
        text = 'x ' * 3000 + 'thickness 1.6 mm ' + 'y ' * 3000
        self.assertIn('thickness 1.6 mm', excerpt(text, 'thickness', 3000)); self.assertLessEqual(len(excerpt(text, 'thickness', 3000)), 3000)

    async def test_pdf_fetch_counts_pages_from_form_feeds(self):
        async def fetch_public(url, **kwargs):
            return url, 'application/pdf', b'%PDF-1.4 fixture'
        calls = []
        async def pdf_text(content, first=1, last=40):
            calls.append((first, last))
            return 'only page\f'  # pdftotext ends every page, including the last, with a form feed
        with patch('server.research.fetch_public', fetch_public), patch('server.research.pdf_text', pdf_text):
            document = await ResearchTool({'enabled': True}).fetch('https://manufacturer.example/drawing.pdf')
        self.assertEqual(document['pages'], 1)
        self.assertEqual(calls, [(1, 400)])
        async def pdf_text_three(content, first=1, last=40):
            return 'one\ftwo\fthree'
        with patch('server.research.fetch_public', fetch_public), patch('server.research.pdf_text', pdf_text_three):
            self.assertEqual((await ResearchTool({'enabled': True}).fetch('https://manufacturer.example/drawing.pdf'))['pages'], 3)

    async def test_live_activity_is_broadcast_but_only_steps_are_saved(self):
        child = Project.create(self.project.path / 'research-tasks', 'research')
        queue = self.runner.subscribe()
        progress = ResearchProgress(self.runner, child, {'part_identity': 'fixture', 'dimensions': ['length']})
        progress.activity('Thinking about the next step')
        progress.consume({'t': 'tool_input_done', 'tool': 'submit_research', 'operation_id': 'report'})
        progress.consume({'t': 'tool_settled', 'operation_id': 'report', 'status': 'failed', 'message': 'Validation failed: summary is required\nReceived arguments: PRIVATE_REPORT'})
        saved = [e for e in self.runner.transcript.read() if e['t'] == 'research_agent']
        self.assertEqual([e['step']['activity'] for e in saved], ['Started', 'Report needs correction'])
        self.assertNotIn('PRIVATE_REPORT', str(saved))
        live = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual([e['activity'] for e in live], ['Starting the investigation', 'Thinking about the next step', 'Report needs correction'])
        self.assertTrue(live[1]['ephemeral']); self.assertNotIn('sources', live[1])
        snapshot = research_snapshot(saved, True, {child.id: progress})
        self.assertEqual(snapshot[-1]['activity'], 'Report needs correction'); self.assertEqual(snapshot[-1]['status'], 'running')
        self.assertEqual(research_snapshot(saved, True, {})[-1]['status'], 'interrupted')

    async def test_pictures_identify_features_but_cannot_document_dimensions(self):
        child = Project.create(self.project.path / 'research-tasks', 'research')
        investigation = Investigation(self.runner, {'part_identity': 'fixture', 'dimensions': ['ports']}, child)
        async def images(tool, query):
            return {'operation': 'images', 'query': query, 'pictures': [{'url': 'https://photos.example/top.jpg', 'title': 'Labelled top view', 'bytes': png_bytes(), 'content_type': 'image/png'}],
                    'notice': 'x'}
        with patch.object(ResearchTool, 'images', images):
            content, failed = await investigation.execute('web_images', {'query': 'pi zero labelled top view'})
        self.assertFalse(failed)
        result = json.loads(content[0]['text'])
        self.assertEqual(result['images'][0]['title'], 'Labelled top view')
        self.assertEqual(content[2]['type'], 'image')
        photo = result['images'][0]['id']
        with self.assertRaisesRegex(ValueError, 'not a rendered page'):
            investigation._dimension({'name': 'w', 'value': 1, 'unit': 'mm', 'datum': 'd', 'source_id': 'web_x', 'quote': '1', 'image_id': photo})
        steps = [e['step']['activity'] for e in self.runner.transcript.read() if e['t'] == 'research_agent' and e.get('step')]
        self.assertTrue(any(s.startswith('Fetched 1 picture') for s in steps))

    async def test_stop_button_cancels_the_child_and_returns_a_tool_error(self):
        from server.dimension_research import ResearchCancelled
        entered = asyncio.Event()
        async def blocked(child, *args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(AgentRunner, '_native_model', blocked):
            task = asyncio.create_task(investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']}))
            await entered.wait()
            agent_id = next(iter(self.runner.investigations))
            with self.assertRaisesRegex(ValueError, 'not running'):
                self.runner.cancel_investigation('missing')
            self.runner.cancel_investigation(agent_id)
            with self.assertRaisesRegex(ResearchCancelled, 'user stopped'):
                await task
        progress = self.runner.snapshot()['research_agents'][-1]
        self.assertEqual(progress['status'], 'cancelled'); self.assertEqual(progress['activity'], 'Research stopped by you')
        self.assertEqual(self.runner.investigations, {})

    async def test_parent_reads_documents_in_parts_and_renders_pages_itself(self):
        calls = 0
        pdf_url = 'https://manufacturer.example/drawing.pdf'
        document = {'kind': 'pdf', 'url': pdf_url, 'requested_url': pdf_url, 'title': 'drawing.pdf', 'links': [], 'headings': [],
                    'text': 'Page one: ordering information and general description. ' * 4 + '\fPage two: BOARD OUTLINE 65 x 30 mm.\fPage three: revision history.\f',
                    'pdf': b'%PDF-fake', 'pages': 3}
        async def fetch(tool, url):
            return dict(document)
        async def pages(content, pages=2, dpi=110, *, first=1, last=None):
            return [{'page': p, 'jpeg': png_bytes((300 + p, 200))} for p in range(first, (last or first) + 1)]
        async def reply(endpoint, messages, tools, **kwargs):
            nonlocal calls
            calls += 1
            try:
                return await steps(messages)
            except Exception:
                import traceback; traceback.print_exc()
                print('TOOL MESSAGES:', [tool_text(m)[:300] for m in messages if m['role'] == 'tool'], flush=True)
                raise
        async def steps(messages):
            if calls == 1:
                name, args = 'research', {'query_or_url': pdf_url, 'focus': 'board outline'}
            elif calls == 2:
                result = last_tool_result(messages)['result']
                self.assertEqual(result['pages'], 3); self.assertIn('BOARD OUTLINE', result['text'])
                name, args = 'research', {'query_or_url': pdf_url, 'focus': '', 'pages': [2, 2]}
            elif calls == 3:
                result = last_tool_result(messages)['result']
                self.assertEqual(result['rendered_pages'], [2, 2]); self.assertEqual(len(result['images']), 1)
                self.assertTrue(any(m['role'] == 'user' and 'image_url' in json.dumps(m) for m in messages), 'the rendered page reaches the model')
                return {'content': 'The outline is 65 x 30 mm on page 2.'}
            return {'content': '', 'tool_calls': [{'id': f'p{calls}', 'name': name, 'arguments': json.dumps(args)}]}
        with patch.object(ResearchTool, 'fetch', fetch), patch('server.research_reader.pdf_page_images', pages), pi_model(self.runner, reply):
            self.runner.start('Model the board case', 'auto')
            async with asyncio.timeout(40):
                while self.runner.phase != 'awaiting': await asyncio.sleep(.02)
            await self.runner.wait_stopped()
        self.assertEqual(calls, 3)
        source = self.runner.research['sources'][-1]
        self.assertEqual((source['kind'], source['opened'], len(source['images'])), ('pdf', True, 1))
        self.assertTrue((self.project.path / 'research-docs' / 'index.json').is_file())
        events = self.runner.transcript.read()
        self.assertTrue(any(e['t'] == 'tool_image' for e in events), 'rendered pages show in the chat')
        self.assertTrue(any(e['t'] == 'research_result' and e.get('pages') == [2, 2] for e in events))

    async def test_cancelled_child_is_stopped_and_card_shows_stopped(self):
        entered = asyncio.Event()
        async def blocked(child, *args):
            child.emit({'t': 'thinking_start'})
            entered.set()
            await asyncio.Event().wait()
        with patch.object(AgentRunner, '_native_model', blocked):
            task = asyncio.create_task(investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']}))
            await entered.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        progress = self.runner.snapshot()['research_agents']
        self.assertEqual(progress[-1]['status'], 'cancelled')
        self.assertIn('finished_at', progress[-1])
        self.assertEqual(self.runner.investigations, {})

    async def test_failed_and_interrupted_research_are_not_shown_as_running(self):
        async def fail(*args): raise RuntimeError('Fixture model unavailable')
        with patch.object(AgentRunner, '_native_model', fail):
            with self.assertRaises(RuntimeError):
                await investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'], 'failed')
        self.runner.emit({'t': 'research_agent', 'agent_id': 'interrupted', 'status': 'running', 'activity': 'Reading', 'started_at': 1})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'], 'interrupted')
        self.runner.web_enabled = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            await investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']})

    async def test_report_save_failure_settles_research_card(self):
        async def finish(*args): return None
        with patch.object(AgentRunner, '_native_model', finish), \
             patch('server.dimension_research.atomic_json', side_effect=OSError('Fixture disk failure')):
            with self.assertRaises(OSError):
                await investigate(self.runner, {'part_identity': 'fixture', 'dimensions': ['length']})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
