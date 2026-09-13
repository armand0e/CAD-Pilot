"""Opt-in image upgrade check: python harness/tests/docker_workspace_upgrade_check.py [image].

Uses a private temporary Docker volume; existing installations are never touched.
The real Pi SDK calls a local deterministic HTTP model, with no external network.
"""
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]

SEED = '''
import os
from pathlib import Path
root = Path('/opt/cadpilot/harness/knowledge')
os.chown(root, 1000, 1000)
for name, data in {
    'personal-notes.md': 'Existing enclosure notes must survive the update.\\n',
    'learned_facts.jsonl': '{"statement":"Previously learned dimension"}\\n',
}.items():
    path = root / name
    path.write_text(data)
    os.chown(path, 1000, 1000)
assert not (root / 'source-workspace.md').exists()
'''

CHECK = '''
import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from docker.smoke import check_source_workspace
from server.projects import Project
from server.source_workspace import SourceWorkspace, SUPPLIED_FILES
from server.knowledge import design_notes

knowledge = Path('/opt/cadpilot/harness/knowledge')
def snapshot():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in knowledge.iterdir()}
before = snapshot()
assert not (knowledge / 'source-workspace.md').exists()
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_source_workspace.WorkspaceUpgradeTests',
    'test_source_workspace.PiWorkspaceTests.test_real_pi_read_write_edit_bash_and_spec_survive_new_bridge',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped
with tempfile.TemporaryDirectory(prefix='cadpilot-upgrade-') as directory:
    asyncio.run(check_source_workspace(Path(directory)))
assert snapshot() == before, 'The upgrade changed persisted knowledge'
print('PASS: old volume without guide; Pi first request, tools, resume and curved CAD build', flush=True)

# A subsequent image update must also supersede a stale guide in an old volume,
# while leaving the user's persisted knowledge untouched.
(knowledge / 'source-workspace.md').write_text('Outdated guide left by a previous image.\\n')
before = snapshot()
with tempfile.TemporaryDirectory(prefix='cadpilot-stale-guide-') as directory:
    work = SourceWorkspace(Project.create(directory, 'appimage-freecad'))
    work.ensure()
    assert work.file('CAD_GUIDE.md').read_bytes() == SUPPLIED_FILES['CAD_GUIDE.md'].read_bytes()
assert 'source-workspace' not in design_notes('workspace')['available']
assert snapshot() == before
print('PASS: stale volume guide cannot replace the installed guide; all existing knowledge preserved', flush=True)
'''


def main():
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                                    sys.argv[1] if len(sys.argv) > 1 else 'cadpilot:paired'], text=True).strip()
    volume = 'cadpilot-upgrade-check-' + uuid.uuid4().hex
    subprocess.run(['docker', 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL)
    base = ['docker', 'run', '--rm', '-i', '--network', 'none', '--mount',
            f'type=volume,src={volume},dst=/opt/cadpilot/harness/knowledge,volume-nocopy',
            '--entrypoint', '/opt/venv/bin/python']
    try:
        subprocess.run(base + ['--user', '0', image, '-'], input=SEED, text=True, check=True)
        flags = ['--cap-drop', 'ALL']
        for option in ('seccomp=unconfined', 'apparmor=unconfined', 'systempaths=unconfined', 'no-new-privileges:true'):
            flags += ['--security-opt', option]
        for name in ('test_source_workspace.py', 'pi_fixture.py'):
            flags += ['--mount', f'type=bind,src={ROOT / "harness/tests" / name},dst=/checks/{name},readonly']
        flags += ['--env', 'PYTHONPATH=/opt/cadpilot/harness:/checks']
        subprocess.run(base + flags + [image, '-'], input=CHECK, text=True, check=True)
    finally:
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
