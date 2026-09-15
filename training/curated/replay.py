"""Execute hand-authored tool traces into quarantine. Does NOT train or edit v3 data."""
import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
sys.path.insert(0, str(ROOT / 'training'))
from server.projects import Project, atomic_json, FILES
from cad_policy.cad_sandbox import sandbox_command
from seed_cases import episodes


async def command(args, log):
    with log.open('wb') as output:
        proc = await asyncio.create_subprocess_exec(*args, stdout=output, stderr=output, start_new_session=True)
        try:
            await asyncio.wait_for(proc.wait(), 90)
        finally:
            if proc.returncode is None:
                import os, signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
    if proc.returncode:
        raise ValueError(log.read_text(errors='replace')[-5000:])


async def verify(project, result, oracle, output, *, strict_edit_oracle=True):
    output.mkdir()
    for name in FILES:
        shutil.copyfile(project.file(result['head'], name), output / name)
    current = 20 if oracle == 'spacer' else 80 if oracle == 'plate-resized' else 90 if oracle == 'enclosure' else 60
    candidates = [p['name'] for p in result['design']['parameters'] if p['value'] == current]
    if len(candidates) != 1:
        raise ValueError('Parameter-edit probe requires one unambiguous independent dimension; review the recipe mapping')
    atomic_json(output / 'task.json', {'oracle': oracle, 'result_object': result['geometry']['result_object'],
        'edit_parameter': candidates[0], 'edit_value': current + (2 if oracle == 'spacer' else 10),
        'strict_edit_oracle': strict_edit_oracle})
    # Both advertised CAD backends actually render the same recipe.
    await command(sandbox_command('openscad', output) + ['/opt/cad/AppRun', '-o', '/work/openscad.stl', '/work/model.scad'], output / 'openscad.log')
    verifier = sandbox_command('freecad', output, extra_binds=[(Path(__file__).with_name('verify_artifact.py'), '/verify.py')]) + ['/opt/cad/usr/bin/python', '/verify.py']
    await command(verifier + ['--meshes'], output / 'mesh-verification.log')
    await command(verifier, output / 'verification.log')
    return json.loads((output / 'verification.json').read_text())


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root', type=Path, default=ROOT / 'runs/curated-cad')
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='replay-', dir=args.output_root))
    print(output, flush=True)
    manifest = {'schema': 'cad-tool-traces-1.0', 'authorship': 'assistant-hand-authored',
                'target': 'native recipe generation and repair; requires a training adapter, NOT cad-trajectory screenshot/action policy',
                'tool_contract': 'compile_design = Project.prepare + Project.commit; internal recipe compiler, not the live planner decision schema',
                'training_authorized': False, 'training_eligible': False, 'on_policy': False,
                'split': 'quarantine', 'held_out_evaluation': False,
                'source_sha256': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ('seed_cases.py', 'replay.py', 'verify_artifact.py')}, 'episodes': []}
    atomic_json(output / 'manifest.json', manifest)
    for episode in episodes():
        project = Project.create(output / 'projects', 'freecad')
        messages, checks = [], []
        for index, authored in enumerate(episode['steps']):
            if authored['user']:
                messages.append({'role': 'user', 'content': authored['user']})
            call_id = f'{episode["id"]}-{index}'
            messages.append({'role': 'assistant', 'content': authored['assistant'],
                # Do not teach the deliberately broken tool call as a correct target.
                'supervise': not bool(authored['expected_error']),
                'tool_calls': [{'id': call_id, 'type': 'function', 'function': {
                    'name': 'compile_design', 'arguments': json.dumps(authored['design'], ensure_ascii=False)}}]})
            head = project.read()['head']
            try:
                stage = await project.prepare(authored['design'])
            except ValueError as error:
                if not authored['expected_error'] or authored['expected_error'] not in str(error):
                    raise
                assert project.read()['head'] == head
                payload = {'ok': False, 'error': str(error), 'previous_head': head, 'revision_preserved': True}
            else:
                if authored['expected_error']:
                    shutil.rmtree(stage)
                    raise AssertionError('Expected failing tool call unexpectedly succeeded')
                result = project.commit(stage, head)
                checked = await verify(project, result, authored['oracle'], output / f'{call_id}-verification')
                checks.append(checked)
                payload = {'ok': True, 'project_id': project.id, 'head': result['head'],
                           'geometry': result['geometry'], 'artifact_sha256': result['revisions'][-1]['sha256']}
            messages.append({'role': 'tool', 'tool_call_id': call_id, 'name': 'compile_design',
                             'content': json.dumps(payload, ensure_ascii=False), 'supervise': False})
        trace = {'schema': manifest['schema'], 'id': episode['id'], 'family': episode['family'],
                 'authorship': manifest['authorship'], 'training_eligible': False,
                 'tool_observations': 'captured from real sandboxed execution', 'messages': messages,
                 # Grader evidence is NOT leaked into the teacher's pre-action context.
                 'verification': checks, 'project_id': project.id}
        target = output / f'{episode["id"]}.json'
        atomic_json(target, trace)
        manifest['episodes'].append({'id': episode['id'], 'family': episode['family'], 'file': target.name,
                                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'verified_successes': len(checks)})
        atomic_json(output / 'manifest.json', manifest)
        print(json.dumps(manifest['episodes'][-1]), flush=True)
    manifest['replay_complete'] = True
    manifest['review_required'] = ['CAD expert review', 'training contract/adapter', 'family-grouped split', 'explicit training authorization']
    atomic_json(output / 'manifest.json', manifest)
    print(json.dumps({'output': str(output), 'episodes': len(manifest['episodes']), 'training_started': False}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
