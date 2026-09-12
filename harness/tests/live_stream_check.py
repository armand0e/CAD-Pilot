"""Opt-in live regression for the reported spacer JSON-generation stall.

Only inference and in-memory operation validation: no CAD, project writes,
desktop input, training or web tools. Evidence is written to a new test folder.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner
from server.operations import OPERATION_SYSTEM, candidate, validate_operation, workspace


async def main():
    root = Path(__file__).resolve().parents[2]
    output = Path(tempfile.mkdtemp(prefix='spacer-stream-', dir=root/'runs/harness-checks'))
    config = yaml.safe_load((root/'harness/config.yaml').read_text())
    runner = AgentRunner(SimpleNamespace(app={'name': 'CAD'}), config)
    runner.turn_id = 'isolated-spacer-probe'
    task = 'Create a hollow cylindrical spacer, 20 mm outer diameter, 10 mm inner diameter, and 15 mm tall.'
    context = {'original_task': task, 'requested_edit': task, 'conversation': [{'role': 'user', 'content': task}],
               'head': None, 'workspace': {'bodies': {}, 'parameters': [], 'operation_count': 0},
               'successful_operations': [], 'geometry': None, 'last_tool_result': None}
    started = time.monotonic(); rejected = []
    try:
        for attempt in range(4):
            raw = ''
            try:
                raw = await runner._chat(config['planner'], [
                    {'role': 'system', 'content': OPERATION_SYSTEM}, {'role': 'user', 'content': json.dumps(context)}],
                    max_tokens=8192, display_operation=True, request_timeout_s=180,
                    response_format={'type': 'json_schema', 'json_schema': {'name': 'cad_operation', 'strict': True,
                                     'schema': runner._operation_schema()}}, chat_template_kwargs=runner._model_thinking())
                proposal = validate_operation(json.loads(raw))
                assert proposal['tool'] == 'create_body', proposal
                candidate(workspace(), proposal)
                break
            except (ValueError, SyntaxError) as error:
                raw = getattr(error, 'raw', raw)
                runner._settle_proposal('failed', str(error))
                rejected.append(str(error))
                if attempt == 3:
                    raise
                context['last_tool_result'] = {'ok': False, 'proposal': raw[:6000], 'error': str(error),
                    'instruction': 'Correct this operation. No geometry has been created.'}
        runner._settle_proposal('cancelled', 'Read-only smoke test: no CAD operation executed.')
        report = {'status': 'passed', 'model': config['planner']['model'], 'proposal': proposal,
                  'rejected': rejected, 'elapsed_s': round(time.monotonic()-started, 2),
                  'cad_executions': 0, 'reasoning_fragments': sum(e['t']=='thinking_delta' for e in runner.events),
                  'tool_input_fragments': sum(e['t']=='tool_input_delta' for e in runner.events),
                  'scope': 'One first operation validated in memory, not a completed CAD part.'}
        (output/'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        (output/'activity.json').write_text(json.dumps(list(runner.events), indent=2))
        print('Evidence: '+str(output), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
