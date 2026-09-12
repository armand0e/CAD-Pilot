"""Opt-in live prompt evaluation: the stock model answers fixed scenarios through the exact
production message builders, once per prompt variant, and deterministic checks score the
decision. Nothing is executed in CAD; no web request is made. Usage:
  harness/.venv/bin/python tests/prompt_eval.py [--variant baseline|current|both] [--only S1,S3]
"""
import argparse, asyncio, json, re, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import agent as agent_module, operations as operations_module, planning as planning_module, dialogue as dialogue_module
from server.agent import AgentRunner, _jpeg_b64, geometry_summary
from server.operations import OPERATION_SCHEMA, candidate, validate_operation, workspace, stored, primitive_bounds, normalize_tool_arguments
from server.planning import parse_plan
from server.projects import Project
from server.research import source
import prompt_baselines

PI_PROJECT = next(iter((ROOT / 'runs/harness-checks/pi-live-ny89012o').glob('project-*/conversation.json')), None)


def op(tool, plan='', **arguments):
    return {'plan': plan, 'tool': tool, 'arguments': arguments}


def params(**values):
    return [{'name': k, 'value': v} for k, v in values.items()]


def pi_research():
    if PI_PROJECT:
        return json.load(PI_PROJECT.open())['research']
    return {'sources': [], 'notes': {'facts': [], 'assumptions': [], 'unknowns': []}}


def pi_search_only():
    research = json.loads(json.dumps(pi_research()))
    research['sources'] = [s for s in research['sources'] if s['kind'] == 'search_result'] or [
        source('https://datasheets.raspberrypi.com/rpi3/raspberry-pi-3-b-plus-mechanical-drawing.pdf', 'Mechanical drawings, PDF', 'Raspberry Pi 3 Model B+ mechanical drawing', 'search_result'),
        source('https://www.raspberrypi-spy.co.uk/2012/03/mechanical-data-dimensions/', 'Raspberry Pi Mechanical Drawings & Dimensions', 'Mechanical data and dimensions of all Pi boards', 'search_result')]
    research['notes'] = {'facts': [], 'assumptions': [], 'unknowns': []}
    return research


def tray_ledger():
    saved = workspace()
    ops = [op('create_body', 'Bottom tray 90x61x12', id='tray', name='Pi 3B case tray', kind='box', dimensions=['tray_l', 'tray_w', 'tray_h'], at=['0', '0', '0'],
              anchor='corner', axis='z', parameters=params(tray_l=90, tray_w=61, tray_h=12, wall=2)),
           op('shell', 'open top, 2 mm walls', body='tray', wall='wall', open_faces=['zmax'], parameters=[]),
           op('hole_pattern', 'four M2.5 holes on the 58x49 pattern', body='tray', face='zmin', u='6', v='6', diameter='hole_d', depth='through',
              count_u=2, count_v=2, pitch_u='58', pitch_v='49', parameters=params(hole_d=2.7))]
    for item in ops:
        saved, design, state = candidate(saved, item)
    return saved, design, state


async def build_geometry(design):
    with tempfile.TemporaryDirectory(prefix='prompt-eval-') as root:
        project = Project.create(root, 'freecad')
        stage = await project.prepare(design)
        return json.loads((stage / 'geometry.json').read_text())


def cutter_in_wall(proposal, saved, state):
    """A proposed cut/hole whose cutter meets a solid wall band of the body."""
    try:
        _, design, next_state = candidate(saved, proposal)
    except ValueError as error:
        return 0, f'rejected: {str(error)[:90]}'
    last = next_state['operations'][-1]
    bands = state['bodies'][proposal['arguments']['body']]['solid_wall_bands_mm']
    for span in last.get('geometry_mm', []):
        numbers = {ax: (float(a), float(b)) for ax, a, b in re.findall(r'([xyz]) (-?[\d.]+)\.\.(-?[\d.]+)', span)}
        for face, (lo, hi) in bands.items():
            axis = face[0]
            a, b = numbers.get(axis, (None, None))
            if a is None:
                continue
            others = [o for o in 'xyz' if o != axis]
            inside = all(numbers[o][0] < state['bodies'][proposal['arguments']['body']]['bounds_mm'][o][1] and numbers[o][1] > state['bodies'][proposal['arguments']['body']]['bounds_mm'][o][0] for o in others)
            if a < hi and b > lo and inside:
                return 1, f'cutter {span} meets wall {face} {lo}..{hi}'
    return 0.3, f'compiles but misses every wall band: {last.get("geometry_mm")}'


def words(text):
    return len(re.findall(r'\S+', text))


class Scenario:
    def __init__(self, key, title, kind, build, score):
        self.key, self.title, self.kind, self.build, self.score = key, title, kind, build, score


async def scenarios():
    saved, design, state = tray_ledger()
    geometry = await build_geometry(design)
    research_full = pi_research()
    brief = json.load(PI_PROJECT.open()).get('design_brief') if PI_PROJECT else 'Two-part Pi 3B case; mounting pattern sourced; port positions unknown.'
    items = []

    def add(key, title, kind, build, score):
        items.append(Scenario(key, title, kind, build, score))

    add('S1', 'fresh Pi case task, nothing researched', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)')],
             intent='make me a raspberry pi 3B case (top and bottom)', ledger=workspace(), state={'bodies': {}, 'parameters': [], 'operation_count': 0},
             geometry=None, research=None, brief=None, feedback=None),
        lambda tool, a, text: (1, 'research keywords') if tool == 'research' and not a['query_or_url'].startswith('http') and words(a['query_or_url']) <= 8
        else (0.7, 'research but a long query') if tool == 'research' and not a['query_or_url'].startswith('http')
        else (0.9, 'asked a question first') if tool == 'ask_question' else (0.4, 'brief without sources') if tool == 'brief' else (0, f'{tool} without research'))
    add('S2', 'search results present, nothing read yet', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)')],
             intent='make me a raspberry pi 3B case (top and bottom)', ledger=workspace(), state={'bodies': {}, 'parameters': [], 'operation_count': 0},
             geometry=None, research=pi_search_only(), brief=None, feedback={'tool': 'research', 'result': {'operation': 'search', 'query': 'raspberry pi 3b mechanical drawing', 'notice': 'Search snippets are leads, NOT verified specifications.'}}, pages=True),
        lambda tool, a, text: (1, 'reads a URL') if tool == 'research' and a['query_or_url'].startswith('http')
        else (0.3, 'searched again') if tool == 'research' else (0.5, 'asked') if tool == 'ask_question' else (0, f'{tool} without reading'))
    add('S3', 'tray built, mounting sourced, port positions unknown', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)')],
             intent='make me a raspberry pi 3B case (top and bottom)', ledger=saved, state=state, geometry=geometry, research=research_full, brief=brief,
             feedback={'tool': 'hole_pattern', 'ok': True, 'head': 'r0003'}, previous_plan='four M2.5 holes on the 58x49 pattern', head='r0003'),
        lambda tool, a, text: (1, f'asked: {a["question"][:60]}') if tool == 'ask_question' and len(a['options']) >= 2
        else (0.8, 'reads a page for port positions') if tool == 'research' and a['query_or_url'].startswith('http')
        else (0.6, 'searches for port positions') if tool == 'research' else (0.5, 'builds the lid first') if tool == 'create_body'
        else (0.2, 'cuts a port at an assumed position') if tool in ('cut', 'hole') else (0.3, tool))
    add('S4', 'fresh A100 fan shroud, nothing researched', 'native',
        dict(task='please make me a fan shroud for my a100', dialogue=[('user', 'please make me a fan shroud for my a100')],
             intent='please make me a fan shroud for my a100', ledger=workspace(), state={'bodies': {}, 'parameters': [], 'operation_count': 0},
             geometry=None, research=None, brief=None, feedback=None),
        lambda tool, a, text: (1, f'asked: {a["question"][:60]}') if tool == 'ask_question' and len(a['options']) >= 2
        else (0.8, 'researched first') if tool == 'research' and not a['query_or_url'].startswith('http') and words(a['query_or_url']) <= 8
        else (0.6, 'researched with a long query') if tool == 'research' else (0.3, 'brief straight away') if tool == 'brief' else (0, f'{tool}: guessed a fan'))
    cavity_error = ('Native CAD build failed (previous revision preserved): operation 4 (cut): cutter removed no material. The cutter (x 30..38, y 26..29, z 3..9) '
                    'sits inside an empty region of the body and touches no solid material; along y through the cutter center, solid material is at 0..2, 59..61. '
                    'Move it into a wall band or extend it through one.')
    bad_cut = op('cut', 'micro-USB opening', body='tray', kind='box', dimensions=['usb_w', '3', 'usb_h'], at=['30', '26', '3'], anchor='corner', axis='z', parameters=params(usb_w=8, usb_h=6))
    add('S5', 'recover a cut that missed the wall', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)')],
             intent='make me a raspberry pi 3B case (top and bottom)', ledger=saved, state=state, geometry=geometry, research=research_full, brief=brief,
             feedback={'tool': 'native_operation', 'ok': False, 'proposal': json.dumps(bad_cut), 'error': cavity_error, 'head_unchanged': 'r0003',
                       'instruction': 'Previous checkpoints preserved. Correct only this operation, or ask. Do not regenerate existing geometry.'},
             previous_plan='micro-USB opening 8x6 mm', head='r0003'),
        lambda tool, a, text, proposal=None: cutter_in_wall(proposal, saved, state) if tool in ('cut', 'hole') else (0.5, 'asked instead') if tool == 'ask_question' else (0, tool))
    conflict = op('replace_operation', 'move the USB cut', index=3, operation=op('hole_pattern', '', body='tray', face='zmin', u='6', v='6', diameter='hole_d', depth='through',
                  count_u=2, count_v=2, pitch_u='58', pitch_v='49', parameters=params(hole_d=2.7)))
    add('S6', 'replace an operation that redeclared a parameter', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)'), ('user', 'make the mounting holes 3 mm')],
             intent='make the mounting holes 3 mm', ledger=saved, state=state, geometry=geometry, research=research_full, brief=brief,
             feedback={'tool': 'native_operation', 'ok': False, 'proposal': json.dumps(conflict),
                       'error': 'Operation 3 (hole_pattern): Parameter hole_d is already declared by operation 3; use the name hole_d in expressions without redeclaring it (set_parameter changes its value)',
                       'head_unchanged': 'r0003', 'instruction': 'Previous checkpoints preserved. Correct only this operation, or ask.'},
             previous_plan='make the holes 3 mm', head='r0003'),
        lambda tool, a, text, proposal=None: (1, 'set_parameter hole_d=3') if tool == 'set_parameter' and a['name'] == 'hole_d' and a['value'] == 3
        else (0.8, 'compiles') if tool == 'replace_operation' and compiles(saved, proposal) else (0, f'{tool}: does not compile'))
    add('S7', 'everything built and inspected; finish honestly', 'native',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)')],
             intent='make me a raspberry pi 3B case (top and bottom)', ledger=saved, state=state, geometry=geometry, research=research_full, brief=brief,
             feedback={'tool': 'inspect', 'ok': True, 'head': 'r0003', 'state': state, 'geometry': geometry_summary(geometry, state),
                       'next': 'If every requested feature is present, call finish. Otherwise add the missing feature.'}, previous_plan='inspect', head='r0003', inspected=True),
        lambda tool, a, text: (1, 'reply labels assumptions') if tool == 'reply' and re.search(r'assum', a['message'], re.I) and not re.search(r'\bfound\b|verified', a['message'], re.I)
        else (0.6, 'reply without labelling assumptions') if tool == 'reply' else (0.8, 'keeps building (lid missing)') if tool in ('create_body', 'ask_question')
        else (0.8, 'reads a page for the missing positions') if tool == 'research' and a['query_or_url'].startswith('http') else (0.3, tool))
    add('P1', 'planner: user says ports are missing', 'planner',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)'), ('assistant', 'Raspberry Pi 3B two-piece case complete. No port/connector cutouts included; add as needed.'), ('user', 'uhh i dont see any port slots or anything')],
             research=research_full, brief=brief),
        lambda decision, plan: (1, 'model, only sourced numbers') if decision == 'model' and not unsupported_numbers(plan['objective'], research_full)
        else (0.6, 'model with invented numbers: ' + ', '.join(unsupported_numbers(plan['objective'], research_full))) if decision == 'model' else (0.4, decision))
    add('P2', 'planner: user doubts the layout', 'planner',
        dict(task='make me a raspberry pi 3B case (top and bottom)', dialogue=[('user', 'make me a raspberry pi 3B case (top and bottom)'), ('assistant', 'Added port cutouts on the front wall for USB, Ethernet, HDMI, power and audio.'), ('user', 'are you sure this is the proper layout of the 3b board? this doesn\'t look like the right layout at all')],
             research=research_full, brief=brief),
        lambda decision, plan: (1, f'{decision}: admits assumptions') if decision in ('respond', 'ask', 'research', 'model') and re.search(r'assum|not verified|unverified|estimat|guess', plan['message'] + plan['objective'], re.I)
        else (0.4, f'{decision}: no admission') if decision in ('respond', 'ask', 'research') else (0.2, decision))
    return items


def unsupported_numbers(text, research):
    """Measurements in text that no research fact supports (the conversation has none)."""
    from server.grounding import unsupported_measurements
    return unsupported_measurements(text, research['notes']['facts'], [])


def compiles(saved, proposal):
    try:
        candidate(saved, proposal); return True
    except ValueError:
        return False


def make_runner(config, build):
    screen = SimpleNamespace(capture_image=lambda: Image.new('RGB', (64, 64), '#333'))
    project = SimpleNamespace(public=lambda: {'head': build.get('head'), 'design': None, 'geometry': build.get('geometry'), 'workspace': None,
                                              'name': 'eval', 'revisions': []}, path=Path(tempfile.mkdtemp(prefix='prompt-eval-runner-')))
    runner = AgentRunner(SimpleNamespace(app={'name': 'FreeCAD'}, engine='hybrid', project=project, screen=screen, manual_changes=False), config)
    runner.task_text = build['task']
    for index, (role, text) in enumerate(build['dialogue']):
        runner.dialogue.consume({'t': 'user' if role == 'user' else 'assistant', 'text': text, 'message': text, 'event_id': f'eval-{index}', 'turn_id': f'eval-turn-{index}'})
    runner.user_messages.extend(text for role, text in build['dialogue'] if role == 'user')
    if build.get('research'):
        runner.research = build['research']
    runner.design_brief = build.get('brief')
    return runner


async def run_variant(name, config, items):
    baseline = name == 'baseline'
    patches = [patch.object(agent_module, 'OPERATION_SYSTEM', prompt_baselines.OPERATION_SYSTEM), patch.object(agent_module, 'DIALOGUE_RULES', prompt_baselines.DIALOGUE_RULES),
               patch.object(agent_module, 'PLAN_SYSTEM', prompt_baselines.PLAN_SYSTEM), patch.object(agent_module, 'PLAN_GUI_RULES', '')] if baseline else []
    # Note: the baseline prompts predate native tool calling; they are compared through the same tool-calling transport.
    for item in patches:
        item.start()
    results = []
    try:
        for scenario in items:
            runner = make_runner(config, scenario.build)
            started = time.monotonic()
            raw, error = '', None
            try:
                if scenario.kind == 'native':
                    b = scenario.build
                    ctx = {'project': runner.session.project, 'expected_head': b.get('head'), 'state': b['state'], 'ledger': b['ledger'], 'geometry': b.get('geometry')}
                    history = [{'role': 'user', 'content': b['intent']}]
                    if b.get('feedback'):
                        history += [{'role': 'assistant', 'content': b.get('previous_plan') or '', 'tool_calls': [{'id': 'prev', 'type': 'function', 'function': {'name': b['feedback'].get('tool', 'inspect'), 'arguments': b['feedback'].get('proposal', '{}') if isinstance(b['feedback'].get('proposal'), str) else '{}'}}]},
                                    {'role': 'tool', 'tool_call_id': 'prev', 'name': b['feedback'].get('tool', 'inspect'), 'content': json.dumps(b['feedback'])[:12000]}]
                    messages = runner._native_messages(history, ctx, research_pages=b.get('pages', False))
                    result = await runner._chat_tools(config['planner'], messages, runner._tool_definitions(), request_timeout_s=240,
                                                      thinking={'reasoning_effort': config['agent'].get('native_reasoning_effort'), 'thinking_token_budget': config['agent'].get('native_thinking_token_budget')})
                    raw = json.dumps(result)
                    if result['tool_calls']:
                        first = result['tool_calls'][0]
                        tool = first['name']
                        arguments = normalize_tool_arguments(tool, json.loads(first['arguments'] or '{}'))
                    else:
                        tool, arguments = 'reply', {'message': result['content']}
                    proposal = {'tool': tool, 'arguments': arguments}
                    try:
                        score, why = scenario.score(tool, arguments, result['content'], proposal=proposal)
                    except TypeError:
                        score, why = scenario.score(tool, arguments, result['content'])
                    decision = tool
                else:
                    messages = runner._planner_messages(_jpeg_b64(Image.new('RGB', (64, 64), '#333')))
                    raw = await runner._chat(config['planner'], messages, max_tokens=6144, display_plan=True, request_timeout_s=240,
                                             response_format=runner._plan_format(), chat_template_kwargs=runner._model_thinking())
                    plan = parse_plan(raw)
                    decision = plan['decision']
                    score, why = scenario.score(decision, plan)
            except Exception as failure:
                error, decision, score, why = str(failure)[:200], 'error', 0, str(failure)[:120]
            reasoning = sum(len(e.get('text', '')) for e in runner.events if e['t'] == 'thinking_delta')
            truncated = any(e['t'] == 'thinking_done' and e.get('status') == 'truncated' for e in runner.events)
            results.append({'scenario': scenario.key, 'title': scenario.title, 'variant': name, 'decision': decision, 'score': score, 'why': why,
                            'seconds': round(time.monotonic() - started, 1), 'reasoning_chars': reasoning, 'truncated': truncated, 'raw': raw[:1500], 'error': error})
            print(f"{name:8s} {scenario.key} {decision:16s} {score:>4} {results[-1]['seconds']:6.1f}s think={reasoning:5d}{' TRUNC' if truncated else ''}  {why}", flush=True)
    finally:
        for item in patches:
            item.stop()
    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', default='both', choices=['baseline', 'current', 'both'])
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / 'harness/config.yaml').read_text())
    config['research']['enabled'] = True
    items = await scenarios()
    if args.only:
        wanted = set(args.only.split(','))
        items = [s for s in items if s.key in wanted]
    out = Path(tempfile.mkdtemp(prefix='prompt-eval-', dir=ROOT / 'runs/harness-checks'))
    results = []
    for name in (['baseline', 'current'] if args.variant == 'both' else [args.variant]):
        results += await run_variant(name, config, items)
    totals = {}
    for r in results:
        totals.setdefault(r['variant'], []).append(r['score'])
    summary = {v: {'mean': round(sum(x) / len(x), 3), 'n': len(x)} for v, x in totals.items()}
    (out / 'results.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1))
    print(json.dumps(summary), 'saved', out)


if __name__ == '__main__':
    asyncio.run(main())
