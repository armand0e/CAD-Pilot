"""Real parent/child Pi sessions with controlled research evidence."""
import asyncio
import json
import io
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock
from PIL import Image

from pi_fixture import pi_model, messages as pi_messages, message_text
from server.agent import AgentRunner
from server.dimension_research import investigate, submit
from server.projects import Project
from server.research import ResearchTool, bounded_result
from server.attachments import image_path, image_provenance, inspect_image, store_image
from server.research_progress import ResearchProgress


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
                    name,args='research',{'query_or_url':'Fixture board rev A dimensions','focus':'Board length and thickness'}
                elif child_calls==2:
                    # The production handoff previously dropped every source,
                    # forcing the child to guess URLs despite a successful search.
                    self.assertIn(self.source['url'],str(messages))
                    self.assertIn('Official fixture drawing',str(messages))
                    name,args='research',{'query_or_url':self.source['url'],'focus':'Board length and thickness'}
                elif child_calls==3:
                    self.assertIn(self.source['text'][:100],str(messages))
                    self.assertIn('web_fixture',str(messages))
                    self.assertIn('drawing.pdf',str(messages))
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
        async def search(tool,query):
            return {'operation':'search','query':query,'sources':[{**self.source,'kind':'search_result','text':'Official fixture drawing'}]}
        async def read(tool,url,focus):
            self.assertEqual(url,self.source['url'])
            return bounded_result({'operation':'read','sources':[{**self.source,'text':self.source['text'][:12000],
                'links':[{'url':'https://manufacturer.example/drawing.pdf','title':'Download drawing'}]}]})
        # Mock only the network boundary. Exercise the real research handler,
        # adapter and Pi loop, and forbid the duplicate legacy model extraction.
        extractor=AsyncMock(side_effect=AssertionError('Pi research must not invoke the legacy extractor'))
        with patch.object(ResearchTool,'search',search),patch.object(ResearchTool,'read',read), \
             patch.object(AgentRunner,'_chat',extractor),pi_model(self.runner,reply):
            self.runner.start('Model the fixture board case','auto')
            async with asyncio.timeout(25):
                while self.runner.phase!='awaiting':await asyncio.sleep(.02)
            await self.runner.wait_stopped()
        self.assertEqual((parent_calls,child_calls),(2,4))
        extractor.assert_not_awaited()
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
        result=submit(self.runner,self.report)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(self.runner.dimension_report['dimensions'],[])
        self.assertIn('search snippet',result['unresolved_dimensions'][0]['issue'])
        self.runner.research['sources']=[{**self.source,'text':'Different model; length not documented.'}]
        result=submit(self.runner,self.report)
        self.assertEqual(self.runner.dimension_report['notes']['facts'],[])
        self.assertIn('absent',result['unresolved_dimensions'][0]['issue'])
        self.runner.web_enabled=False
        with self.assertRaisesRegex(ValueError,'disabled'):
            await investigate(self.runner,{'part_identity':'fixture','dimensions':['length']})

    async def test_pi_submits_large_mixed_report_once_and_preserves_drawing_provenance(self):
        calls=0
        buffer=io.BytesIO();Image.new('RGB',(320,240),'white').save(buffer,'PNG')
        pdf={**self.source,'id':'web_pdf','kind':'pdf','text':'No extractable text: drawing page attached.'}
        async def read(tool,url,focus):
            self.assertEqual(url,pdf['url'])
            return {'operation':'read','sources':[dict(pdf),dict(self.source)],'page_images':[{'jpeg':buffer.getvalue(),'page':1}]}
        async def reply(endpoint,messages,tools,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                name,args='research',{'query_or_url':pdf['url'],'focus':'outline and hole dimensions'}
            elif calls==2:
                result=json.loads([m for m in messages if m['role']=='tool'][-1]['content'].split('\n')[0])
                # The tool result includes explicit document -> image provenance.
                image_id=result['result']['sources'][0]['images'][0]['id']
                name,args='view_image',{'id':image_id,'crop':[10,20,160,200]}
            elif calls==3:
                result=json.loads([m for m in messages if m['role']=='tool'][-1]['content'].split('\n')[0])
                crop_id=result['id']
                rows=[{**self.report['dimensions'][0],'name':f'text dimension {i}'} for i in range(16)]
                rows += [{**rows[0],'name':'drawing board width','value':30,'source_id':'web_pdf','quote':'30','image_id':crop_id},
                         {**rows[0],'name':'unsupported thickness','quote':'Thickness is 2 mm.'}]
                name,args='submit_research',{**self.report,'dimensions':rows,'assumptions':['Long contextual explanation. '*35]}
            else:
                self.assertEqual(calls,4,'The report must not be sent repeatedly')
                self.assertEqual(tools,[], 'Pi should only finish the response once the report is saved')
                self.assertIn('Report saved',str(messages))
                self.assertIn('unresolved_dimensions',str(messages))
                self.assertIn('already saved',str(messages))
                return {'content':'Findings returned with one unresolved measurement.'}
            tool_calls=[{'id':f'child-{calls}','name':name,'arguments':json.dumps(args)}]
            if calls==3:
                tool_calls.append({'id':'queued-after-report','name':'research',
                                   'arguments':json.dumps({'query_or_url':'https://should-not-be-requested.example','focus':'already done'})})
            return {'content':'','tool_calls':tool_calls}
        with patch.object(ResearchTool,'read',read),pi_model(self.runner,reply):
            async with asyncio.timeout(25):
                result=await investigate(self.runner,{'part_identity':'fixture','dimensions':['outline','mounting pattern']})
        self.assertEqual(calls,4)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['documented_dimensions'],16)
        self.assertEqual(result['visual_dimensions'],1)
        self.assertEqual(len(result['unverified_dimensions']),1)
        self.assertEqual(result['unverified_dimensions'][0]['row'],18)
        evidence=result['dimensions'][-1]['evidence']
        self.assertEqual(evidence['page'],1)
        self.assertEqual(evidence['crops'],[[10,20,160,200]])
        self.assertEqual(evidence['verification'],'visual_reading_requires_confirmation')
        self.assertEqual(image_provenance(self.project.path,evidence['image_id'])['crops'],evidence['crops'])
        self.assertTrue(image_path(self.project.path,evidence['original_image_id']).exists())
        self.assertEqual(len(self.runner.research['notes']['facts']),16)
        self.assertTrue(any('Unverified drawing reading' in s for s in self.runner.research['notes']['assumptions']))
        restored=AgentRunner(self.runner.session,self.runner.config)
        self.addAsyncCleanup(restored.wait_stopped)
        self.assertEqual(restored.research['notes'],self.runner.research['notes'])
        progress=[e for e in self.runner.transcript.read() if e['t']=='research_agent']
        self.assertEqual(progress[-1]['status'],'incomplete')
        self.assertEqual(progress[-1]['documented_dimensions'],16)
        self.assertEqual(progress[-1]['visual_dimensions'],1)
        self.assertTrue(any(e['activity']=='Findings saved with unresolved measurements' for e in progress))

    async def test_numeric_image_citation_resolves_only_recorded_source_and_never_becomes_text_fact(self):
        buffer=io.BytesIO();Image.new('RGB',(100,80),'white').save(buffer,'PNG')
        pdf={**self.source,'kind':'pdf','text':'No extractable text'}
        self.runner._store_page_images([{'jpeg':buffer.getvalue(),'page':2}],pdf)
        self.runner.research['sources']=[pdf]
        drawing=pdf['images'][0]['id']
        crop=inspect_image(self.project.path,drawing,[0,0,50,50])
        nested=inspect_image(self.project.path,crop['id'],[0,0,25,25])
        row={**self.report['dimensions'][0],'source_id':nested['id'],'quote':'65'}
        result=submit(self.runner,{**self.report,'dimensions':[row]})
        self.assertEqual(result['visual_dimensions'],1)
        self.assertEqual(self.runner.dimension_report['notes']['facts'],[])
        evidence=self.runner.dimension_report['dimensions'][0]['evidence']
        self.assertEqual(evidence['crops'],[[0,0,50,50],[0,0,25,25]])
        self.assertEqual(evidence['original_image_id'],drawing)
        user_image=store_image(self.project.path/'attachments',buffer.getvalue(),'unscaled user photo')
        self.runner.research['sources']=[{**pdf,'images':[]}]
        result=submit(self.runner,{**self.report,'dimensions':[{**row,'source_id':pdf['id'],'image_id':user_image['id']}]})
        self.assertEqual(result['documented_dimensions'],0)
        self.assertEqual(result['visual_dimensions'],0)
        self.assertIn('not linked',result['unresolved_dimensions'][0]['issue'])

    async def test_validation_errors_remain_visible_during_the_next_model_request(self):
        progress=ResearchProgress(self.runner,self.project,{'part_identity':'fixture','dimensions':['length']})
        progress.consume({'t':'tool_input_done','tool':'submit_research','operation_id':'report'})
        progress.consume({'t':'tool_settled','operation_id':'report','status':'failed',
                          'message':'Validation failed: summary is required\nReceived arguments: PRIVATE_REPORT'})
        progress.consume({'t':'phase','phase':'modeling'})
        progress.consume({'t':'thinking_start'})
        self.assertEqual(progress.value['activity'],'Report needs correction')
        self.assertIn('summary is required',progress.value['detail'])
        self.assertNotIn('PRIVATE_REPORT',progress.value['detail'])
        progress.consume({'t':'research_report','documented_dimensions':1})
        progress.consume({'t':'phase','phase':'modeling'})
        self.assertEqual(progress.value['activity'],'Finishing the research report')
        self.assertEqual(progress.value['detail'],'')

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
