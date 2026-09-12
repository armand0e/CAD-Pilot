"""Open a verified seed's multi-part FCStd through the production presenter."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
from server.detect import detect_apps
from server.sessions import SessionManager
from server.projects import Project, atomic_json
from server.presentation import present_revision
from server.edit_guard import native_edit_blocker
from server.native import read_native_state


async def main():
    replay = Path(sys.argv[1]).resolve()
    manifest = json.loads((replay / 'manifest.json').read_text())
    assert manifest['replay_complete'] and not manifest['training_eligible']
    trace = json.loads((replay / 'vented-enclosure.json').read_text())
    project = Project(replay / 'projects', trace['project_id'])
    output = Path(tempfile.mkdtemp(prefix='curated-preview-', dir=ROOT / 'runs/harness-checks'))
    manager = SessionManager(state_root=output)
    print(output, flush=True)
    try:
        app = next(a.to_json() for a in detect_apps() if a.id == 'appimage-freecad')
        app['native_project'] = True
        session = await asyncio.to_thread(manager.create, app)
        session.project = project
        await present_revision(session)
        await asyncio.sleep(1)
        assert await native_edit_blocker(session) is None
        native = read_native_state(session.state_dir)
        assert native and native['document']['object_count'] > 0
        session.screen.capture_image().save(output / 'enclosure.png')
        atomic_json(output / 'result.json', {'success': True, 'project_id': project.id,
                    'head': project.read()['head'], 'native_state': native})
        print(json.dumps({'success': True, 'output': str(output)}), flush=True)
    finally:
        manager.close_all()


if __name__ == '__main__':
    asyncio.run(main())
