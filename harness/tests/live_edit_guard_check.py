"""Actual FreeCAD signals, input, revision presentation, and background-edit guard."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
sys.path.insert(0, str(ROOT / 'training/curated'))
from server.sessions import SessionManager
from server.detect import detect_apps
from server.native import read_native_state
from server.edit_guard import native_edit_blocker, acknowledge_saved, note_input
from server.presentation import present_revision
from server.projects import Project, atomic_json
from seed_cases import PLATE


async def fixture(session, action):
    request = session.state_dir / 'guard-fixture-request'
    request.write_text(action)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        receipt = session.state_dir / 'guard-fixture-receipt'
        if receipt.exists() and receipt.read_text() == action:
            await asyncio.sleep(.8)
            return
        await asyncio.sleep(.1)
    raise AssertionError(f'Fixture failed: {receipt.read_text() if receipt.exists() else "no receipt"}')


async def main():
    output = Path(tempfile.mkdtemp(prefix='edit-guard-', dir=ROOT / 'runs/harness-checks'))
    print(output, flush=True)
    app = next(a.to_json() for a in detect_apps() if a.id == 'appimage-freecad')
    app['native_project'] = True
    app['command'] += [str(Path(__file__).with_name('edit_guard_fixture.FCMacro'))]
    manager = SessionManager(state_root=output)
    try:
        s = await asyncio.to_thread(manager.create, app)
        deadline = time.monotonic() + 25
        while not read_native_state(s.state_dir) and time.monotonic() < deadline:
            await asyncio.sleep(.2)
        assert read_native_state(s.state_dir), 'Observer did not start'
        s.screen.click(650, 700); note_input(s)
        assert await native_edit_blocker(s) is None, read_native_state(s.state_dir)
        s.project = Project.create(output / 'projects', 'freecad')
        s.project.commit(await s.project.prepare(PLATE), None)
        await present_revision(s)
        assert await native_edit_blocker(s) is None, read_native_state(s.state_dir)
        await fixture(s, 'view'); note_input(s, 'agent')
        assert await native_edit_blocker(s) is None, read_native_state(s.state_dir)
        await fixture(s, 'edit'); note_input(s)
        assert await native_edit_blocker(s) is not None, 'Real spreadsheet edit was not protected'
        atomic_json(output / 'edited-state.json', read_native_state(s.state_dir))
        await acknowledge_saved(s)
        assert await native_edit_blocker(s) is None, 'Explicit branch failed'
        await fixture(s, 'background')
        assert await native_edit_blocker(s) is not None, 'Background model hidden by empty active document'
        atomic_json(output / 'background-state.json', read_native_state(s.state_dir))
        s.screen.capture_image().save(output / 'final.png')
        assert s.project.read()['head'] == 'r0001'
        atomic_json(output / 'result.json', {'success': True, 'fresh_click': True, 'revision_open': True,
            'camera_selection': True, 'real_edit_protected': True, 'background_document_protected': True,
            'explicit_branch': True, 'saved_revision_unchanged': True})
        print(json.dumps({'success': True, 'output': str(output)}), flush=True)
    finally:
        manager.close_all()


if __name__ == '__main__':
    asyncio.run(main())
