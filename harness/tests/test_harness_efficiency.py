"""Context, source-tool transitions and durable specification/input regressions."""
import asyncio
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pi_fixture import pi_model, messages, message_text
from server.agent import AgentRunner
from server.operations import SOURCE_INCOMPATIBLE_TOOLS
from server.pi_agent import PiBridge
from server.projects import Project, atomic_json
from server.source_workspace import SourceWorkspace
from server.transcript import Transcript


class HarnessEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.project = Project.create(self.temp.name, 'freecad')
        self.work = SourceWorkspace(self.project)
        session = SimpleNamespace(project=self.project, engine='hybrid', manual_changes=False,
                                  app={'name':'FreeCAD'}, screen=SimpleNamespace(release_inputs=None), state_dir=None)
        self.runner = AgentRunner(session, {'agent':{'native_operations':True}, 'planner':{}, 'policy':{}, 'research':{'enabled':False}})
        self.addAsyncCleanup(self.runner.wait_stopped)
        self.ctx = {'project':self.project, 'expected_head':None, 'ledger':{}, 'state':{}, 'geometry':{}, 'saved':{'design':None}}
        self.bridge = PiBridge(self.runner, self.ctx)
        self.runner.emit({'t':'user', 'event_id':'first', 'text':'Bracket with an 8 mm wall.'})
        self.bridge.record_inputs(self.work)

    def update(self, value):
        return self.work.update_spec(value, self.work.spec()['version'], patch=True)

    def head(self, revision):
        data=self.project.read();data['head']=revision;atomic_json(self.project.path/'project.json',data)

    async def test_inspect_does_not_duplicate_spec_or_send_full_research(self):
        self.update({'requirements':[{'id':'initial-request','text':'Requirement detail. '*7000}]})
        page='PRIVATE_FULL_PAGE_'+'Source text. '*7000
        self.runner.research={'notes':{'facts':[]}, 'sources':[{'id':'web_test','kind':'page','title':'Drawing','url':'https://example.com/drawing','text':page}]}
        result=self.bridge.inspect()
        encoded=json.dumps(result)
        self.assertLessEqual(len(encoded),24000)
        self.assertNotIn('PRIVATE_FULL_PAGE_',encoded)
        self.assertNotIn('specification',self.work.describe())
        self.assertIn('Read the complete specification',encoded)
        self.assertIn('Requirement detail. '*100,self.work.file('design-spec.json').read_text())
        self.assertEqual(self.runner.research['sources'][0]['text'],page)

    async def test_oversized_inputs_are_recoverable_and_context_files_do_not_dirty_source(self):
        before=self.work.dirty()
        self.runner.emit({'t':'user','event_id':'large','text':'long input '*40000})
        self.bridge.record_inputs(self.work)
        overview=self.work.spec_context()
        self.assertLessEqual(len(json.dumps(overview)),24000)
        complete=json.loads(self.work.file(overview['context_file']).read_text())
        self.assertEqual(complete['pending_inputs'][-1]['text'],'long input '*40000)
        self.assertEqual(self.work.dirty(),before)
        self.assertFalse(any('.cadpilot-context' in f['path'] for f in self.work.files()))

    async def test_partial_edits_preserve_other_rows_and_explicit_retirement_is_required(self):
        value=self.update({'requirements':[{'id':'hole','text':'4 mm hole','origin':'user','evidence':['input:first']}]})
        value=self.update({'requirements':[{'id':'hole','text':'5 mm hole'}]})
        self.assertEqual([r['id'] for r in value['requirements']],['initial-request','hole'])
        self.assertEqual(value['requirements'][0]['text'],'Bracket with an 8 mm wall.')
        bad=copy.deepcopy(value);bad['requirements']=[];bad['addressed_inputs']=['input:first']
        with self.assertRaisesRegex(ValueError,'Do not remove'):
            self.work.update_spec(bad,value['version'])
        with self.assertRaisesRegex(ValueError,'Retiring'):
            self.update({'requirements':[{'id':'hole','status':'retired'}]})
        self.runner.emit({'t':'user','event_id':'correction','text':'Remove the hole.'});self.bridge.record_inputs(self.work)
        value=self.update({'requirements':[{'id':'hole','status':'retired','retirement':{'reason':'User removed hole','evidence':['input:correction']}}], 'addressed_inputs':['input:correction']})
        self.assertEqual(value['requirements'][1]['status'],'retired')
        self.assertEqual(len(self.work.inputs()),2)
        self.assertEqual(value['requirements'][0]['status'],'open')
        self.runner.emit({'t':'user','event_id':'unlinked','text':'Add another feature.'});self.bridge.record_inputs(self.work)
        with self.assertRaisesRegex(ValueError,'not yet cited by any row: input:unlinked'):
            self.update({'addressed_inputs':['input:unlinked']})

    async def test_measurement_visual_and_file_verification_have_distinct_scopes(self):
        self.head('r0001')
        measure=self.work.save_evidence({'query':'objects','revision':'r0001','objects':[{'id':'Bracket','bounds_mm':[30,20,8]}]})
        render=self.work.save_evidence({'query':'render','revision':'r0001','image':{'id':'render.png'}})
        row={'id':'initial-request','status':'verified','features':['Bracket'],'evidence':['input:first',measure['id']],
             'verification':{'kind':'measurement','evidence':measure['id'],'field':'/objects/0/bounds_mm/2','expected':8,'tolerance':.01}}
        self.update({'requirements':[row]})
        self.assertTrue(self.work.spec_context()['verification_checks']['initial-request']['valid'])
        for changes, error in [({'expected':4},'check failed'),({'field':'/created'},'metadata'),({'expected':float('nan')},'finite')]:
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,error):
                self.update({'requirements':[{**row,'verification':{**row['verification'],**changes}}]})
        with self.assertRaisesRegex(ValueError,'body/face'):
            self.update({'requirements':[{**row,'features':['OtherBody']}]})
        with self.assertRaisesRegex(ValueError,'not a render'):
            self.update({'requirements':[{**row,'evidence':['input:first',render['id']],
                'verification':{**row['verification'],'evidence':render['id']}}]})
        self.update({'decisions':[{'id':'appearance','text':'Appearance checked','origin':'assumed','evidence':[render['id']],
            'status':'verified','verification':{'kind':'visual','evidence':render['id'],'note':'Opening is visible'}}]})
        self.update({'requirements':[{'id':'editable','text':'Keep editable source','origin':'user','evidence':['input:first'],
            'status':'verified','verification':{'kind':'task','file':'model.py'}}]})
        self.head('r0002')
        self.update({'coordinates':'New datum note'})  # Stale checks do not block unrelated work.
        checks=self.work.spec_context()['verification_checks']
        self.assertFalse(checks['initial-request']['valid']);self.assertFalse(checks['appearance']['valid'])
        self.assertTrue(checks['editable']['valid'])
        self.work.file('model.py').write_text('# changed source')
        self.assertFalse(self.work.spec_context()['verification_checks']['editable']['valid'])

    async def test_legacy_verified_rows_are_preserved_but_not_claimed_as_checked(self):
        value=self.work.spec();value['requirements'][0]['status']='verified'
        atomic_json(self.project.path/'spec-state.json',value);atomic_json(self.work.file('design-spec.json'),value)
        self.update({'coordinates':'Updated coordinate description'})
        self.assertIn('initial-request',self.work.spec_context()['stale_requirements'])
        value=self.update({'requirements':[{'id':'initial-request','text':'Updated dimension'}]})
        self.assertEqual(value['requirements'][0]['status'],'implemented')

    async def test_verification_uses_its_own_evidence_link_and_accepts_revision_labels(self):
        self.head('r0001')
        evidence=self.work.save_evidence({'query':'section','revision':'r0001','object':'Tag',
            'contours':[{'bounds':{'bounds_mm':[4,4,0],'max_mm':[8,12,3]}}]})
        value=self.update({'requirements':[{'id':'initial-request','status':'verified',
            'features':['hole cylinder face Tag:Face4 (r0001)'],
            'verification':{'kind':'measurement','evidence':evidence['id'],
                'field':'/contours/0/bounds/bounds_mm/0','expected':4,'tolerance':.01}}]})
        self.assertIn(evidence['id'],value['requirements'][0]['evidence'])
        self.assertEqual(self.work.spec_context()['verification_checks']['initial-request']['subjects'],['Tag'])

    async def test_bad_verification_fields_return_all_row_errors_and_numeric_selectors(self):
        self.head('r0001')
        evidence=self.work.save_evidence({'query':'objects','revision':'r0001',
            'objects':[{'id':'Bracket','bounds_mm':[30,20,8]}]})
        check={'kind':'measurement','evidence':evidence['id'],'field':'/objects/0','expected':8}
        with self.assertRaises(ValueError) as caught:
            self.update({'requirements':[{'id':'initial-request','status':'verified','verification':check},
                {'id':'other','text':'Other dimension','origin':'assumed','evidence':[],'status':'verified','verification':check}]})
        self.assertIn('initial-request:',str(caught.exception));self.assertIn('other:',str(caught.exception))
        self.assertIn('/objects/0/bounds_mm/2 = 8',str(caught.exception))
        self.assertEqual(self.work.spec()['version'],1)

    async def test_new_input_cursor_skips_stream_history_and_unchanged_writes(self):
        for i in range(100):self.runner.emit({'t':'answer_delta','text':'stream fragment','answer_id':'answer'})
        with patch.object(self.runner.transcript,'read',side_effect=AssertionError('full transcript read')), \
             patch('server.source_workspace.atomic_json',wraps=atomic_json) as writer:
            for _ in range(5):self.bridge.record_inputs(self.work)
            self.assertFalse(writer.called)
            self.runner.emit({'t':'answer','event_id':'second','summary':'Make it blue'})
            self.bridge.record_inputs(self.work)
            self.assertEqual(sum(c.args[0].name=='input-evidence.json' for c in writer.call_args_list),1)
        fresh=PiBridge(self.runner,self.ctx);fresh.record_inputs(self.work)
        self.assertEqual([r['id'] for r in self.work.inputs()],['input:first','input:second'])

    async def test_old_transcript_schema_migrates_with_all_ui_events_preserved(self):
        path=Path(self.temp.name)/'old.sqlite3'
        events=[{'t':'user','event_id':'old','text':'Old request'},{'t':'answer_delta','event_id':'delta','text':'partial'}]
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL)')
            db.executemany('INSERT INTO events(id,payload) VALUES (?,?)',[(e['event_id'],json.dumps(e)) for e in events])
        transcript=Transcript(path);cursor,inputs=transcript.input_events()
        self.assertEqual(inputs,[events[0]]);self.assertEqual(transcript.read(),events)
        transcript.append({'t':'answer','event_id':'reply','summary':'Correction'})
        self.assertEqual(transcript.input_events(cursor)[1][0]['event_id'],'reply')
        self.assertEqual(len(Transcript(path).read()),3)

    async def test_real_pi_hides_legacy_tools_immediately_after_source_build(self):
        requests=[]
        async def reply(endpoint,messages,tools,**kwargs):
            requests.append({t['function']['name'] for t in tools})
            if len(requests)==1:return {'content':'','tool_calls':[{'id':'build','name':'cad_build','arguments':'{"entrypoint":"model.py"}'}]}
            return {'content':'Source model ready.'}
        saved={'head':'r0001','design':{'format':'source-v1'},'geometry':{'parts':[]}}
        async def build(*args,**kwargs):return saved,None
        with patch('server.workspace_tools.build_revision',build),patch.object(PiBridge,'views',return_value=[]),pi_model(self.runner,reply):
            self.runner.start('Build a source model','auto')
            async with asyncio.timeout(20):
                while self.runner.phase!='awaiting':await asyncio.sleep(.02)
            await self.runner.wait_stopped()
        self.assertTrue(SOURCE_INCOMPATIBLE_TOOLS & requests[0])
        self.assertFalse(SOURCE_INCOMPATIBLE_TOOLS & requests[1])
        self.assertTrue({'edit','cad_build','cad_inspect','ask_question'} <= requests[1])
        results=[m for m in messages(self.runner) if m.get('role')=='toolResult']
        self.assertFalse(any(r.get('isError') for r in results),[message_text(r) for r in results])
