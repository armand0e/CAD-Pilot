"""Export modeling-agent trajectories (task, dialogue, plan, operation, outcome) from harness
projects as JSONL for later fine-tuning. Quarantined by default: every record carries
training_eligible=false until a human review marks it otherwise.

Usage: python scripts/export_trajectories.py --root harness/projects --root runs/harness-checks/native-projects --out runs/trajectories/export.jsonl
"""
import argparse, hashlib, json, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harness'))
from server.operations import OPERATION_SYSTEM  # noqa: E402


def project_records(project):
    conversation = json.loads((project / 'conversation.json').read_text()) if (project / 'conversation.json').exists() else {}
    meta = json.loads((project / 'project.json').read_text())
    transcript = []
    if (project / 'chat.sqlite3').exists():
        db = sqlite3.connect(project / 'chat.sqlite3')
        transcript = [json.loads(r[0]) for r in db.execute('select payload from events order by seq')]
    reviews = [e for e in transcript if e.get('t') == 'native_review']
    outcome = reviews[-1]['status'] if reviews else 'unreviewed'
    steps = []
    for revision in meta.get('revisions', []):
        ws = project / revision['id'] / 'workspace.json'
        geo = project / revision['id'] / 'geometry.json'
        if not ws.exists():
            continue
        operations = json.loads(ws.read_text())['operations']
        if not operations:
            continue
        geometry = json.loads(geo.read_text()) if geo.exists() else {}
        last = operations[-1]
        steps.append({'revision': revision['id'], 'operation': {k: v for k, v in last.items() if k != 'plan'}, 'plan': last.get('plan', ''),
                      'ledger_length': len(operations), 'geometry': {k: geometry.get(k) for k in ('solid_count', 'volume_mm3', 'bounds_mm')}})
    attempts = []
    for path in sorted((project / 'attempts').glob('*.json')) if (project / 'attempts').exists() else []:
        attempt = json.loads(path.read_text())
        attempts.append({'parent': attempt.get('parent'), 'raw': attempt.get('raw_recipe', '')[:4000], 'error': attempt.get('error', '')[:1000]})
    return {'project': project.name, 'task': conversation.get('task'), 'dialogue': conversation.get('dialogue', []),
            'design_brief': conversation.get('design_brief'), 'research_facts': (conversation.get('research') or {}).get('notes', {}).get('facts', []),
            'library_facts': conversation.get('library_facts', []), 'steps': steps, 'rejected_attempts': attempts, 'outcome': outcome,
            'prompt_sha256': hashlib.sha256(OPERATION_SYSTEM.encode()).hexdigest()[:16], 'exported_at': time.time(),
            'training_eligible': False, 'review': 'pending human review; assistant-generated trajectory'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', action='append', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open('w') as handle:
        for root in args.root:
            for project in sorted(Path(root).glob('*')):
                if not (project / 'project.json').exists():
                    continue
                record = project_records(project)
                if record['steps']:
                    handle.write(json.dumps(record) + '\n'); count += 1
    print(f'{count} trajectories -> {out}')


if __name__ == '__main__':
    main()
