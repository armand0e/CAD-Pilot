"""Protocol, persistence, structured limits and actual model-stream regressions."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner, GuidanceChanged
from server.chat_stream import display_message, JsonStreamGuard, ModelStreamError
from server.transcript import Transcript
from server.planning import parse_plan
from server.research import ResearchTool, ResearchError, bounded_result, source
from server.browser_research import browser_search, browser_read, search_attempt, local_page, SEARCH_SCRIPT, READ_SCRIPT, relevant_results


class ChatRecordsTests(unittest.TestCase):
    def test_json_guard_preserves_indentation_escaped_text_and_unicode(self):
        raw = json.dumps({'message': 'A "quote"\nUnicode 🛠 ' + ' ' * 600, 'items': [1, 2]}, indent=8)
        guard = JsonStreamGuard()
        for i, char in enumerate(raw):
            guard.feed(char, raw[:i+1])

    def test_json_guard_rejects_literal_control_characters_inside_strings(self):
        guard = JsonStreamGuard()
        raw = '{"position": ["],\n'
        with self.assertRaisesRegex(ModelStreamError, 'unescaped control') as error:
            guard.feed(raw, raw)
        self.assertEqual(error.exception.raw, raw)

    def test_transcript_reopens_with_stable_event_and_source_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'chat.sqlite3'
            transcript=Transcript(path)
            event={'t':'research_result','event_id':'result-1','turn_id':'turn-1','operation_id':'search-1',
                   'sources':[source('https://example.com/spec','Spec','An actual snippet','search_result')]}
            transcript.append(event);transcript.append(event)
            self.assertEqual(Transcript(path).read(),[event])

    def test_source_identity_does_not_depend_on_snippet_or_page_content(self):
        a=source('https://example.com/spec#dimensions','Search title','Indexed snippet','search_result')
        b=source('https://example.com/spec','Page title','Actual extracted page text','page')
        self.assertEqual(a['id'],b['id'])
        self.assertFalse(a['opened']);self.assertTrue(b['opened'])
        self.assertIn('T',a['retrievedAt']);self.assertIsNone(a.get('published_at'))

    def test_oversized_output_stays_valid_json_and_preserves_urls(self):
        rows=[source(f'https://example.com/{i}?q='+('a'*1900),'A'*6000,'An actual snippet '*2000,'search_result',links=[{'url':'https://example.com/'+'b'*1900,'title':'C'*2000}]*20) for i in range(8)]
        result=bounded_result({'operation':'search','query':'large results','sources':rows})
        encoded=json.dumps(result,ensure_ascii=False)
        self.assertLessEqual(len(encoded),24000);self.assertTrue(json.loads(encoded)['truncation']['truncated'])
        for row in result['sources']:
            self.assertEqual(row['url'],next(s['url'] for s in rows if s['id']==row['id']))

    def test_partial_json_handles_escapes_unicode_and_never_projects_actions(self):
        text='A "quoted" dimension\n12 mm 🛠 [web_123456789abc]'
        raw=json.dumps({'decision':'respond','message':text,'objective':'','expected_result':'','wait_seconds':0})
        previous=''
        for i in range(len(raw)+1):
            value=display_message(raw[:i])
            if value is not None:
                self.assertTrue(value.startswith(previous),(i,value,previous))
                self.assertTrue(text.startswith(value));previous=value
        self.assertEqual(previous,text)
        self.assertIsNone(display_message('{"decision":"model","message":"build secret"'))
        self.assertIsNone(display_message('{"nested":{"decision":"respond","message":"not public"'))

    def test_product_search_does_not_accept_raspberry_fruit(self):
        self.assertFalse(relevant_results('Raspberry Pi 3 dimensions',[{'title':'Raspberry | fruit and nutrition'}]))

    def test_duplicate_decisions_cannot_change_a_streamed_answer_into_an_action(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            parse_plan('{"decision":"respond","message":"An answer","decision":"act","objective":"Click","expected_result":"Click","wait_seconds":0}')


class Fragments(httpx.AsyncByteStream):
    def __init__(self, content, *, pause=False, finish='stop'):
        self.content=content;self.pause=pause;self.finish=finish;self.closed=False;self.waiting=asyncio.Event()
    async def __aiter__(self):
        for start in range(0,len(self.content),7):
            payload={'choices':[{'delta':{'content':self.content[start:start+7]}}]}
            yield ('data: '+json.dumps(payload)+'\n\n').encode()
            await asyncio.sleep(0)
        self.waiting.set()
        if self.pause:await asyncio.Event().wait()
        if self.finish:
            yield ('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':self.finish}]})+'\n\n').encode()
        yield b'data: [DONE]\n\n'
    async def aclose(self):self.closed=True


class ReasoningFragments(Fragments):
    async def __aiter__(self):
        for text in ['The user approved ', 'provisional connector sizes. ', 'I can choose editable values.']:
            yield ('data: '+json.dumps({'choices':[{'delta':{'reasoning_content':text}}]})+'\n\n').encode()
            await asyncio.sleep(0)
        async for chunk in super().__aiter__():yield chunk


class ChatStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_whitespace_stall_closes_stream_without_executing_partial_tool(self):
        runner = self.runner()
        stream = Fragments('{"tool":"create_body","arguments":{"position":[' + ' \n' * 6000, pause=True)
        with self.transport(stream), self.assertRaisesRegex(ModelStreamError, '256 consecutive') as error:
            await runner._chat(runner.config['planner'], [], display_operation=True,
                               response_format={'type': 'json_schema'})
        self.assertTrue(stream.closed)
        self.assertLess(len(error.exception.raw), 350)
        self.assertFalse(any(e['t'] in ('intent', 'tool_input_done', 'step_done') for e in runner.events))
        failed = next(e for e in runner.events if e['t'] == 'tool_settled')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(error.exception.operation_identity['operation_id'], failed['operation_id'])

    async def test_planner_returns_stall_error_to_model_and_recovers(self):
        runner = self.runner(); requests = []; client = httpx.AsyncClient
        plan = {'decision': 'respond', 'message': 'Ready.', 'objective': '', 'expected_result': '', 'wait_seconds': 0}
        def handle(request):
            requests.append(json.loads(request.content))
            stream = Fragments('{' + ' ' * 600 if len(requests) == 1 else json.dumps(plan))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)):
            await runner._plan_intent('fixture')
        self.assertEqual(len(requests), 2)
        self.assertIn('256 consecutive', requests[1]['messages'][-1]['content'])
        self.assertTrue(requests[1]['messages'][-2]['content'].startswith('{'))
        self.assertEqual(runner.plan_details['message'], 'Ready.')

    async def test_vendor_whitespace_bound_is_opt_in_and_only_for_json(self):
        runner = self.runner(); requests = []; client = httpx.AsyncClient
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Fragments('{}'))
        configured = runner.config['planner'] | {'structured_output_whitespace_pattern': r'[ \n\t]{0,8}'}
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)):
            await runner._chat(configured, [], display_operation=True, response_format={'type': 'json_object'})
            await runner._chat(runner.config['planner'], [], display_operation=True, response_format={'type': 'json_object'})
            await runner._chat(configured, [], display_activity='Status')
        self.assertEqual(requests[0]['structured_outputs']['whitespace_pattern'], r'[ \n\t]{0,8}')
        self.assertTrue(requests[0]['structured_outputs']['json_object'])
        self.assertNotIn('structured_outputs', requests[1])
        self.assertNotIn('structured_outputs', requests[2])

    def runner(self):
        endpoint={'base_url':'http://test/v1','model':'fixture'}
        runner=AgentRunner(SimpleNamespace(app={'name':'CAD'}),{'agent':{},'planner':endpoint,'policy':endpoint,'research':{'enabled':True}})
        runner.turn_id='turn-1'
        return runner

    def transport(self,stream):
        client=httpx.AsyncClient
        def handle(request):
            self.assertTrue(json.loads(request.content)['stream'])
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=stream)
        return patch('server.agent.httpx.AsyncClient',side_effect=lambda **kwargs:client(transport=httpx.MockTransport(handle),**kwargs))

    async def test_provider_public_answer_streams_before_completion_and_validates(self):
        runner=self.runner();reply='Use **2 mm walls**. Dimensions remain provisional.'
        plan={'decision':'respond','objective':'','expected_result':'','message':reply,'wait_seconds':0}
        stream=Fragments(json.dumps(plan))
        with self.transport(stream):await runner._plan_intent('fixture-image')
        deltas=[e for e in runner.events if e['t']=='answer_delta']
        self.assertGreater(len(deltas),3);self.assertEqual(''.join(e['text'] for e in deltas),reply)
        ids={e['answer_id'] for e in runner.events if e['t'].startswith('answer_')};self.assertEqual(len(ids),1)
        self.assertEqual([e for e in runner.events if e['t']=='answer_done'][0]['status'],'completed')
        self.assertEqual(runner._streamed_reply,reply);self.assertTrue(stream.closed)

    async def test_reasoning_and_tool_input_arrive_before_execution_with_stable_identity(self):
        runner=self.runner();raw=json.dumps({'tool':'create_body','arguments':{'name':'Case'}})
        stream=ReasoningFragments(raw,pause=True)
        with self.transport(stream):
            task=asyncio.create_task(runner._chat(runner.config['planner'],[],display_operation=True))
            await stream.waiting.wait()
            thinking=[e for e in runner.events if e['t']=='thinking_delta']
            self.assertEqual(len(thinking),3)
            self.assertEqual([e['offset'] for e in thinking],[0,18,47])
            deltas=[e for e in runner.events if e['t']=='tool_input_delta']
            self.assertEqual(''.join(e['text'] for e in deltas),raw)
            self.assertFalse(any(e['t'] in ('intent','step_done','tool_settled') for e in runner.events))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(stream.closed)
        self.assertEqual(next(e for e in runner.events if e['t']=='tool_settled')['status'],'cancelled')

    async def test_input_block_end_does_not_report_a_completed_tool(self):
        runner=self.runner();stream=ReasoningFragments('{"tool":"inspect","arguments":{}}')
        with self.transport(stream):raw=await runner._chat(runner.config['planner'],[],display_operation=True)
        proposal=runner._proposal_stream.copy()
        self.assertTrue(json.loads(raw))
        self.assertTrue(any(e['t']=='tool_input_done' for e in runner.events))
        self.assertFalse(any(e['t']=='tool_settled' for e in runner.events))
        runner.emit({'t':'intent',**proposal,'step':1,'text':'Inspect','tool':'native_model'})
        runner.emit({'t':'step_done','step':1})
        self.assertEqual(next(e for e in runner.events if e['t']=='step_done')['operation_id'],proposal['operation_id'])

    async def test_reasoning_only_stop_is_explicit_and_no_hidden_placeholder_is_emitted(self):
        runner=self.runner();stream=ReasoningFragments('',pause=True)
        with self.transport(stream):
            task=asyncio.create_task(runner._inference(runner._chat(runner.config['planner'],[],display_operation=True)))
            await stream.waiting.wait();runner.stop()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        self.assertEqual(next(e for e in runner.events if e['t']=='thinking_done')['status'],'cancelled')
        self.assertFalse(any(e['t']=='tool_input_start' for e in runner.events))

    async def test_endpoint_reasoning_budget_only_applies_when_enabled(self):
        runner=self.runner();client=httpx.AsyncClient;requests=[]
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Fragments('{"tool":"inspect","arguments":{}}'))
        endpoint=runner.config['planner']|{'reasoning_effort':'low','thinking_token_budget':1024}
        with patch('server.agent.httpx.AsyncClient',side_effect=lambda **kwargs:client(transport=httpx.MockTransport(handle),**kwargs)):
            for enabled in (True,False):
                await runner._chat(endpoint,[],display_operation=True,chat_template_kwargs={'enable_thinking':enabled})
                runner._settle_proposal('cancelled','Test does not execute tools')
        self.assertEqual(requests[0]['thinking_token_budget'],1024)
        self.assertEqual(requests[0]['reasoning_effort'],'low')
        self.assertNotIn('thinking_token_budget',requests[1])

    async def test_activity_stays_running_while_structured_result_is_still_arriving(self):
        runner=self.runner();stream=Fragments('{"summary":"Still decoding',pause=True)
        with self.transport(stream):
            task=asyncio.create_task(runner._chat(runner.config['planner'],[],display_activity='Checking requirements'))
            await stream.waiting.wait()
            self.assertFalse(any(e['t']=='thinking_done' for e in runner.events))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(next(e for e in runner.events if e['t']=='thinking_done')['status'],'cancelled')

    async def test_stop_cancels_http_read_and_keeps_partial_answer(self):
        runner=self.runner();stream=Fragments('{"decision":"respond","message":"An unfinished answer',pause=True)
        with self.transport(stream):
            task=asyncio.create_task(runner._inference(runner._plan_intent('fixture')))
            await stream.waiting.wait();runner.stop()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        self.assertTrue(stream.closed)
        self.assertEqual([e for e in runner.events if e['t']=='answer_done'][0]['status'],'cancelled')

    async def test_truncated_transport_does_not_mark_answer_complete(self):
        runner=self.runner();stream=Fragments('{"decision":"respond","message":"Incomplete',finish=None)
        with self.transport(stream),self.assertRaisesRegex(ValueError,'before completion'):
            await runner._stream_plan(runner.config['planner'],[],10,{})
        self.assertEqual([e for e in runner.events if e['t']=='answer_done'][0]['status'],'failed')

    async def test_disabling_web_removes_availability_and_rejects_cached_execution(self):
        runner=self.runner();runner.set_web_enabled(False)
        self.assertIn('unavailable',runner._research_instructions())
        self.assertNotIn('research',runner._plan_format()['json_schema']['schema']['properties']['decision']['enum'])
        self.assertFalse(any(v['properties']['tool']['enum']==['research'] for v in runner._operation_schema()['anyOf']))
        with patch.object(runner.research_tool,'search',AsyncMock()) as search:
            await runner._research_step('fan dimensions','')
        search.assert_not_awaited()
        error=next(e for e in runner.events if e['t']=='research_error')
        start=next(e for e in runner.events if e['t']=='research_start')
        self.assertEqual(start['operation_id'],error['operation_id']);self.assertIn('disabled',error['message'])

    async def test_web_setting_cancels_active_browser_work(self):
        runner=self.runner();runner.active=True;entered=asyncio.Event();closed=asyncio.Event()
        async def search(query):
            entered.set()
            try:await asyncio.Event().wait()
            finally:closed.set()
        with patch.object(runner.research_tool,'search',search):
            task=asyncio.create_task(runner._research_step('fan dimensions',''))
            await entered.wait();runner.set_web_enabled(False)
            with self.assertRaises(GuidanceChanged):await asyncio.wait_for(task,2)
        self.assertTrue(closed.is_set());self.assertEqual(runner.events[-1]['t'],'research_cancelled')

    async def test_search_cannot_demote_fetched_evidence_and_page_changes_drop_stale_facts(self):
        runner=self.runner();url='https://example.com/spec'
        page=source(url,'Drawing','Mounting pitch is 41.7 mm.','page')
        fact={'source_id':page['id'],'statement':'Pitch is 41.7 mm.','quote':page['text']}
        runner.research={'sources':[page],'notes':{'facts':[fact],'assumptions':[],'unknowns':[]}}
        with patch.object(runner.research_tool,'search',AsyncMock(return_value={'operation':'search','sources':[source(url,'Drawing','Indexed snippet','search_result')]})):
            await runner._research_step('motor pitch','')
        self.assertEqual(runner.research['sources'][0]['kind'],'page')
        self.assertEqual(runner.research['notes']['facts'],[fact])
        changed=source(url,'New drawing','Different variant: pitch is 39 mm.','page')
        with patch.object(runner.research_tool,'read',AsyncMock(return_value={'operation':'read','sources':[changed]})), \
             patch.object(runner,'_chat',AsyncMock(side_effect=RuntimeError('note extractor unavailable'))):
            await runner._research_step(url,'')
        self.assertEqual(runner.research['notes']['facts'],[])
        self.assertTrue(any(e['t']=='research_result' and e['operation']=='read' for e in runner.events))

    async def test_cad_operation_identity_survives_steering(self):
        runner=self.runner();runner.emit({'t':'intent','step':1,'text':'Open a sketch'})
        started=runner.events[-1]
        runner.turn_id='new-guidance';runner.emit({'t':'step_superseded','step':1})
        self.assertEqual(runner.events[-1]['operation_id'],started['operation_id'])
        self.assertEqual(runner.events[-1]['turn_id'],'turn-1')

    async def test_public_ui_history_is_not_replayed_as_structured_provider_tool_history(self):
        runner=self.runner();runner.emit({'t':'assistant','message':'An older public response'})
        runner.research={'sources':[source('https://example.com/a','Spec','Actual page evidence','page')],
                         'notes':{'facts':[],'assumptions':[],'unknowns':[]}}
        # Future model context intentionally retains bounded source evidence and
        # the existing conversation, not transcript-only raw diagnostics.
        context=runner._research_context()
        self.assertEqual(context['sources'][0]['text'],'Actual page evidence')
        self.assertNotIn('events',context)


class BrowserSearchContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_challenge_and_empty_dom_have_distinct_diagnostics(self):
        page=SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=200)),
            wait_for_timeout=AsyncMock(),inner_text=AsyncMock(return_value='Verify you are human'),evaluate=AsyncMock(return_value=[]))
        with self.assertRaises(ResearchError) as challenge:
            await search_attempt(page,'bing','https://www.bing.com/search?q=','motor')
        self.assertEqual(challenge.exception.code,'challenge');page.evaluate.assert_not_awaited()
        page.inner_text.return_value='Search results'
        with self.assertRaises(ResearchError) as empty:
            await search_attempt(page,'bing','https://www.bing.com/search?q=','motor')
        self.assertEqual(empty.exception.code,'no_results')

    async def test_real_chromium_context_closes_on_cancellation(self):
        entered=asyncio.Event();captured=[]
        async def operation():
            async with local_page() as page:
                captured.append((page,page.context.browser));entered.set()
                await asyncio.Event().wait()
        task=asyncio.create_task(operation())
        await asyncio.wait_for(entered.wait(),15);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,5)
        page,browser=captured[0]
        self.assertTrue(page.is_closed());self.assertFalse(browser.is_connected())

    async def test_engines_are_merged_until_enough_unique_results(self):
        calls=[]
        @asynccontextmanager
        async def page(**kwargs):yield SimpleNamespace(research_errors=[])
        async def attempt(page,name,endpoint,query):
            calls.append(name)
            if name=='brave':raise ResearchError('access challenge',code='challenge')
            if name=='bing':return [{'url':f'https://example.com/{i}','title':f'Result {i}','snippet':'s'} for i in range(5)]+[{'url':'https://shared.example.com/','title':'Shared','snippet':'s'}]
            return [{'url':'https://shared.example.com','title':'Shared again','snippet':'s'},{'url':'https://other.example.com/x','title':'Other','snippet':'s'}]
        with patch('server.browser_research.local_page',page),patch('server.browser_research.search_attempt',attempt):
            provider,rows,warnings=await browser_search('motor drawing')
        # A challenged engine is retried once; a thin engine is topped up by the next; duplicates collapse.
        self.assertEqual(calls,['brave','brave','bing','duckduckgo']);self.assertEqual(provider,'bing+duckduckgo');self.assertEqual(len(warnings),2)
        self.assertEqual(len(rows),7);self.assertEqual({r['engine'] for r in rows},{'bing','duckduckgo'})
        calls.clear()
        async def plenty(page,name,endpoint,query):
            calls.append(name);return [{'url':f'https://{name}.example.com/{i}','title':str(i),'snippet':'s'} for i in range(12)]
        with patch('server.browser_research.local_page',page),patch('server.browser_research.search_attempt',plenty):
            provider,rows,warnings=await browser_search('motor drawing')
        self.assertEqual(calls,['brave']);self.assertEqual(len(rows),12)

    async def test_all_engine_errors_are_structured_and_no_sources_invented(self):
        @asynccontextmanager
        async def page(**kwargs):yield SimpleNamespace(research_errors=[])
        with patch('server.browser_research.local_page',page),patch('server.browser_research.search_attempt',AsyncMock(side_effect=ResearchError('no relevant results',code='no_results'))):
            with self.assertRaises(ResearchError) as raised:await browser_search('motor drawing')
        # An empty engine is not retried with the identical query.
        self.assertEqual(len(raised.exception.diagnostics),3)
        self.assertEqual([d['engine'] for d in raised.exception.diagnostics],['brave','bing','duckduckgo'])

    async def test_cancel_search_and_navigation_closes_operation_context(self):
        for call in (lambda:browser_search('motor'),lambda:browser_read('https://example.com/spec')):
            entered=asyncio.Event();closed=asyncio.Event()
            async def goto(*args,**kwargs):entered.set();await asyncio.Event().wait()
            fake=SimpleNamespace(goto=goto,on=lambda *a:None,research_errors=[])
            @asynccontextmanager
            async def page(**kwargs):
                try:yield fake
                finally:closed.set()
            with patch('server.browser_research.local_page',page):
                task=asyncio.create_task(call());await entered.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
            self.assertTrue(closed.is_set())

    async def test_dom_parsers_and_page_metadata(self):
        from playwright.async_api import async_playwright
        fixtures={
            'duckduckgo':'<div class="result result--ad"><a class="result__a" href="https://ad.example">Ad</a></div><div class="result"><a class="result__a" href="https://example.com/spec">Motor X</a><div class="result__snippet">Drawing dimensions</div></div>',
            'bing':'<li class="b_algo"><h2><a href="https://example.com/spec">Motor X</a></h2><div class="b_caption"><p>Drawing dimensions</p></div></li>',
            'brave':'<div id="results"><div data-type="web"><a href="https://example.com/spec">Motor X</a><div class="snippet-description">Drawing dimensions</div></div></div>'}
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page()
            try:
                for engine,html in fixtures.items():
                    await page.set_content(html);rows=await page.evaluate(SEARCH_SCRIPT,engine)
                    self.assertEqual(len(rows),1);self.assertEqual(rows[0]['snippet'],'Drawing dimensions')
                await page.set_content('<title>Motor drawing</title><meta property="article:published_time" content="2025-02-03"><main><h1>Motor X</h1><p>2024-01-01 2024-02-01 2024-03-01</p></main><nav>Ignore this</nav>')
                extracted=await page.evaluate(READ_SCRIPT)
                self.assertEqual(extracted['published_at'],'2025-02-03');self.assertEqual(extracted['headings'],['Motor X'])
                self.assertIn('several dates',extracted['temporal_warning']);self.assertNotIn('Ignore this',extracted['text'])
            finally:await browser.close()


class ToolCallStreamTests(unittest.IsolatedAsyncioTestCase):
    """Native tool calling over the real SSE transport: reasoning, content and tool-call deltas."""
    async def test_provider_ignoring_stream_preserves_content_tools_and_usage(self):
        runner = AgentRunner(SimpleNamespace(app={'name': 'CAD'}), {'agent': {}, 'planner': {}, 'policy': {}})
        response = {'choices': [{'message': {'content': 'Changing it.', 'tool_calls': [
            {'id': 'call_a', 'type': 'function', 'function': {'name': 'set_parameter', 'arguments': '{"name":"wall","value":1.2}'}}]},
            'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 123, 'completion_tokens': 20}}
        original = httpx.AsyncClient
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)), **kw)):
            result = await runner._chat_tools({'base_url': 'http://test/v1', 'model': 'fixture'}, [], [])
        self.assertEqual(result['content'], 'Changing it.')
        self.assertEqual(result['tool_calls'][0]['name'], 'set_parameter')
        self.assertEqual(result['streams'][0]['operation_id'], 'call_a')
        self.assertEqual(runner.context_usage['prompt_tokens'], 123)

    async def test_tool_call_deltas_are_accumulated_and_streamed_to_the_ui(self):
        from types import SimpleNamespace as NS
        runner = AgentRunner(NS(app={'name': 'CAD'}), {'agent': {}, 'planner': {'model': 'fixture', 'base_url': 'http://test/v1'}, 'policy': {}})
        chunks = [{'delta': {'reasoning_content': 'Think. '}}, {'delta': {'content': '\n\n'}}, {'delta': {'content': 'Changing the wall'}}, {'delta': {'content': '\n\n'}},
                  {'delta': {'content': 'to 1.2 mm.'}}, {'delta': {'content': '\n\n'}},
                  {'delta': {'tool_calls': [{'index': 0, 'id': 'call_a', 'type': 'function', 'function': {'name': 'set_parameter', 'arguments': '{"name": "wa'}}]}},
                  {'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'll", "value": 1.2}'}}]}},
                  {'delta': {'tool_calls': [{'index': 1, 'id': 'call_b', 'type': 'function', 'function': {'name': 'review', 'arguments': '{}'}}]}},
                  {'delta': {}, 'finish_reason': 'tool_calls'}]
        body = ''.join('data: ' + json.dumps({'choices': [c]}) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body.encode())
        original = httpx.AsyncClient
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw)):
            result = await runner._chat_tools(runner.config['planner'], [{'role': 'user', 'content': 'thinner'}], [{'type': 'function', 'function': {'name': 'set_parameter', 'parameters': {}}}], request_timeout_s=30)
        self.assertEqual(result['content'], 'Changing the wall\n\nto 1.2 mm.')  # inner blank lines kept, outer ones never shown
        self.assertEqual([e['text'] for e in runner.events if e['t'] == 'answer_delta'], ['Changing the wall', '\n\nto 1.2 mm.'])
        self.assertEqual([(c['id'], c['name'], c['arguments']) for c in result['tool_calls']], [('call_a', 'set_parameter', '{"name": "wall", "value": 1.2}'), ('call_b', 'review', '{}')])
        self.assertEqual(result['finish_reason'], 'tool_calls')
        self.assertEqual(requests[0]['tool_choice'], 'auto'); self.assertIn('tools', requests[0]); self.assertNotIn('response_format', requests[0])
        kinds = [e['t'] for e in runner.events]
        self.assertIn('answer_start', kinds); self.assertIn('answer_done', kinds)
        self.assertEqual(kinds.count('tool_input_start'), 2); self.assertEqual(kinds.count('tool_input_done'), 2)
        self.assertEqual(''.join(e['text'] for e in runner.events if e['t'] == 'tool_input_delta' and e['operation_id'] == 'call_a'), '{"name": "wall", "value": 1.2}')
        self.assertEqual(result['streams'][0]['operation_id'], 'call_a')
