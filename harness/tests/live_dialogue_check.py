"""Opt-in stock-model regression: propose, validate, NEVER execute CAD or web tools.

Uses an isolated empty workspace and the reported clarification sequence. This is
not a fit benchmark or a complete Raspberry Pi case build.
"""
import asyncio
import argparse
import json
from pathlib import Path
import tempfile
import time
import sqlite3
from types import SimpleNamespace

from PIL import Image
import yaml

from test_dialogue import events
from server.agent import AgentRunner, _jpeg_b64
from server.dialogue import DIALOGUE_RULES
from server.operations import OPERATION_SYSTEM, validate_operation, workspace, candidate
from server.transcript import Transcript


async def main(source_project=None):
    root = Path(__file__).resolve().parents[2]
    output = Path(tempfile.mkdtemp(prefix='dialogue-live-', dir=root/'runs/harness-checks'))
    config = yaml.safe_load((root/'harness/config.yaml').read_text())
    config['agent']['request_timeout_s'] = 180
    agent = AgentRunner(SimpleNamespace(app={'name':'CAD','id':'fixture'},engine='hybrid'), config)
    agent.transcript = Transcript(output/'chat.sqlite3')
    # Only a read-only empty-workspace view. No prepare, commit, screen or browser.
    agent.session.project = SimpleNamespace(public=lambda: {'head':None,'design':None,'geometry':None})
    agent.task_text = 'Create a 60 × 40 × 8 mm plate with four holes.'
    for event in events(): agent.emit(event)
    if source_project:
        # Read only: reproduce the last user reply with its REAL preceding
        # questions and previously retrieved evidence, but not the failing ask
        # emitted after it. Never attach this runner to the source project.
        source_project = source_project.resolve()
        saved = json.loads((source_project/'conversation.json').read_text())
        with sqlite3.connect((source_project/'chat.sqlite3').as_uri()+'?mode=ro',uri=True) as connection:
            history = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM events ORDER BY seq')]
        last_user = max(i for i,e in enumerate(history) if e['t']=='user')
        agent.dialogue.messages.clear()
        for event in history[:last_user+1]: agent.dialogue.consume(event)
        agent.task_text = saved['task']
        agent.research = saved.get('research',agent.research)
        agent.history_lines = saved.get('history',[])
        agent._research_calls = sum(e['t']=='research_start' for e in history[:last_user+1])
        (output/'input-context.json').write_text(json.dumps({'dialogue':agent._conversation_context(),'research':agent.research},indent=2))
    agent.turn_id = 'stock-probe'
    start = time.monotonic()
    image = _jpeg_b64(Image.new('RGB',(640,400),'#292825'))
    try:
        intent = await agent._plan_intent(image)
        print(json.dumps({'plan':agent.plan_details}), flush=True)
        assert agent.plan_details['decision'] == 'model', 'Model still asks/avoids the approved modeling request'
        messages = [{'role':'system','content':OPERATION_SYSTEM+DIALOGUE_RULES+agent._research_instructions()},
            {'role':'user','content':json.dumps({'original_task':agent.task_text,'conversation':agent._conversation_context(),
                'requested_edit':intent,'head':None,'workspace':{'bodies':{},'parameters':[],'operation_count':0},
                'successful_operations':[],'geometry':None,'last_tool_result':None,'research':agent._research_context(),
                'inspected_current_revision':False})}]
        rejected=[]
        for attempt in range(4):
            raw = await agent._chat(config['planner'], messages, max_tokens=8192, display_operation=True, request_timeout_s=180,
                response_format={'type':'json_schema','json_schema':{'name':'cad_operation','strict':True,'schema':agent._operation_schema()}},
                chat_template_kwargs=agent._model_thinking())
            try:
                proposal = validate_operation(json.loads(raw))
                assert proposal['tool'] == 'create_body', proposal
                _, design, state = candidate(workspace(), proposal)
                break
            except ValueError as error:
                rejected.append(str(error));agent._settle_proposal('failed',str(error))
                if attempt==3:raise
                context=json.loads(messages[1]['content'])
                context['last_tool_result']={'tool':'native_operation','ok':False,'proposal':raw[:6000],
                    'error':str(error),'head_unchanged':None,'state':context['workspace'],
                    'instruction':'Previous checkpoints preserved. Correct only this operation. Do not regenerate existing geometry.'}
                messages[1]['content']=json.dumps(context)
        agent._settle_proposal('cancelled','Read-only probe ended before any CAD execution.')
        history = agent.transcript.read()
        report = {'status':'passed','model':config['planner']['model'],'plan':agent.plan_details,'proposal':proposal,
                  'compiled_features':len(design['features']),'state':state,'cad_executions':0,'web_executions':0,
                  'reasoning_fragments':sum(e['t']=='thinking_delta' for e in history),
                  'tool_input_fragments':sum(e['t']=='tool_input_delta' for e in history),
                  'rejected_proposals':rejected,
                  'elapsed_s':round(time.monotonic()-start,2),
                  'scope':'One approved-dialogue continuation on an empty disposable workspace; not a full case or fit test'}
        (output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'output':str(output),**report},indent=2),flush=True)
    finally:
        (output/'activity.json').write_text(json.dumps(agent.transcript.read(),indent=2))
        print('Evidence: '+str(output),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-project',type=Path)
    asyncio.run(main(parser.parse_args().source_project))
