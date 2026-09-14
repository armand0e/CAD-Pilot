"""Real parent/child Pi sessions with controlled research evidence."""
import asyncio
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pi_fixture import pi_model, messages as pi_messages, message_text
from server.agent import AgentRunner
from server.dimension_research import investigate, submit
from server.projects import Project


class DimensionResearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Project.create(self.temp.name,'freecad')
        session=SimpleNamespace(project=self.project,engine='hybrid',manual_changes=False,
                                app={'name':'FreeCAD'},screen=SimpleNamespace(release_inputs=None),state_dir=None)
        self.runner=AgentRunner(session,{'agent':{'native_operations':True},'planner':{},'policy':{},'research':{'enabled':True}})
        self.addAsyncCleanup(self.runner.wait_stopped)
        self.source={'id':'web_fixture','url':'https://manufacturer.example/drawing','title':'Official fixture drawing',
                     'kind':'page','domain':'manufacturer.example','text':'Fixture board length is 65 mm. PRIVATE_FULL_INVESTIGATION '+ 'page details '*5000}
        self.report={'part_identity':'Fixture board rev A','summary':'Length documented; thickness is unknown.',
                     'dimensions':[{'name':'board length','value':65,'unit':'mm','datum':'X extent, board origin at mounting hole 1',
                                    'source_id':'web_fixture','quote':'Fixture board length is 65 mm.'}],
                     'assumptions':[],'unknowns':['Board thickness is not given.']}

    async def test_separate_pi_research_context_has_no_cad_tools_and_returns_only_cited_report(self):
        parent_calls=0;child_calls=0;child_tools=[]
        async def reply(endpoint,messages,tools,**kwargs):
            nonlocal parent_calls,child_calls
            is_child='dimension researcher, a separate Pi' in str(messages[0])
            if is_child:
                child_tools.extend(t['function']['name'] for t in tools)
                child_calls+=1
                if child_calls==1:
                    name,args='research',{'query_or_url':self.source['url'],'focus':'Board length and thickness'}
                elif child_calls==2:
                    name,args='submit_research',self.report
                else:return {'content':'Research recorded.'}
            else:
                parent_calls+=1
                if parent_calls==1:
                    name,args='research_dimensions',{'part_identity':'Fixture board rev A','dimensions':['board length','thickness'],'context':'Do not mix board revisions.'}
                else:
                    self.assertNotIn('PRIVATE_FULL_INVESTIGATION',str(messages))
                    self.assertIn('Fixture board length is 65 mm.',str(messages))
                    return {'content':'I have the documented length; thickness remains unknown.'}
            return {'content':'','tool_calls':[{'id':f'{"child" if is_child else "parent"}-{child_calls if is_child else parent_calls}',
                                               'name':name,'arguments':json.dumps(args)}]}
        async def research(child,objective,focus,**kwargs):
            self.assertTrue(child.research_profile)
            child.research['sources']=[self.source]
            child.execution_feedback={'sources':[self.source]}
            child.emit({'t':'research_start','operation':'read','query':objective})
            child.emit({'t':'research_result','sources':[self.source]})
        with patch.object(AgentRunner,'_research_step',research),pi_model(self.runner,reply):
            self.runner.start('Model the fixture board case','auto')
            async with asyncio.timeout(25):
                while self.runner.phase!='awaiting':await asyncio.sleep(.02)
            await self.runner.wait_stopped()
        self.assertEqual((parent_calls,child_calls),(2,3))
        self.assertIn('research',child_tools);self.assertIn('submit_research',child_tools)
        self.assertFalse(set(child_tools)&{'cad_build','create_body','bash','read','write','edit','research_dimensions','ask_question'})
        self.assertIsNone(self.project.read()['head'])
        result=[m for m in pi_messages(self.runner) if m.get('role')=='toolResult'][-1]
        self.assertFalse(result.get('isError'),message_text(result))
        text=message_text(result)
        self.assertIn('investigation_id',text);self.assertNotIn('PRIVATE_FULL_INVESTIGATION',text)
        self.assertLess(len(text),6000)
        tasks=list((self.project.path/'research-tasks').iterdir());self.assertEqual(len(tasks),1)
        self.assertTrue((tasks[0]/'dimension-report.json').exists())
        self.assertIn('PRIVATE_FULL_INVESTIGATION',''.join(p.read_text() for p in (tasks[0]/'pi/sessions').glob('*.jsonl')))
        self.assertIn('65 mm',self.runner.research['notes']['facts'][0]['statement'])
        displayed=[e for e in self.runner.transcript.read() if e['t']=='research_result']
        self.assertEqual(displayed[-1]['sources'][0]['id'],'web_fixture')
        self.assertNotIn('PRIVATE_FULL_INVESTIGATION',str(displayed))
        progress=[e for e in self.runner.transcript.read() if e['t']=='research_agent']
        self.assertEqual(progress[0]['status'],'running')
        self.assertEqual(progress[-1]['status'],'completed')
        self.assertEqual(progress[-1]['documented_dimensions'],1)
        self.assertEqual(progress[-1]['pages_read'],1)
        self.assertTrue(any(e['activity']=='Reading a source' for e in progress))
        self.assertNotIn('PRIVATE_FULL_INVESTIGATION',str(progress))
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'],'completed')
        from fastapi.testclient import TestClient
        from server.app import app
        with patch('server.app._project',return_value=self.project):
            response=TestClient(app,base_url='http://localhost').get(f'/api/projects/{self.project.id}/investigations/{tasks[0].name}')
            self.assertEqual(response.status_code,200,response.text)

    async def test_search_snippets_and_invented_quotes_cannot_be_reported_as_dimensions(self):
        self.runner.research['sources']=[{**self.source,'kind':'search_result'}]
        with self.assertRaises(ValueError):submit(self.runner,self.report)
        self.runner.research['sources']=[{**self.source,'text':'Different model; length not documented.'}]
        with self.assertRaises(ValueError):submit(self.runner,self.report)
        self.runner.web_enabled=False
        with self.assertRaisesRegex(ValueError,'disabled'):
            await investigate(self.runner,{'part_identity':'fixture','dimensions':['length']})

    async def test_cancelled_child_is_stopped_and_sidebar_stays_stopped(self):
        entered=asyncio.Event()
        async def blocked(child,*args):
            child.emit({'t':'thinking_start'})
            entered.set()
            await asyncio.Event().wait()
        with patch.object(AgentRunner,'_native_model',blocked):
            task=asyncio.create_task(investigate(self.runner,{'part_identity':'fixture','dimensions':['length']}))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        progress=self.runner.snapshot()['research_agents']
        self.assertEqual(progress[-1]['status'],'cancelled')
        self.assertIn('finished_at',progress[-1])

    async def test_failed_and_interrupted_research_are_not_shown_as_running(self):
        async def fail(*args):raise RuntimeError('Fixture model unavailable')
        with patch.object(AgentRunner,'_native_model',fail):
            with self.assertRaises(RuntimeError):
                await investigate(self.runner,{'part_identity':'fixture','dimensions':['length']})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'],'failed')
        self.runner.emit({'t':'research_agent','agent_id':'interrupted','status':'running','activity':'Reading','started_at':1})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'],'interrupted')

    async def test_report_save_failure_settles_research_card(self):
        async def finish(*args):return None
        with patch.object(AgentRunner,'_native_model',finish), \
             patch('server.dimension_research.atomic_json',side_effect=OSError('Fixture disk failure')):
            with self.assertRaises(OSError):
                await investigate(self.runner,{'part_identity':'fixture','dimensions':['length']})
        self.assertEqual(self.runner.snapshot()['research_agents'][-1]['status'],'failed')
