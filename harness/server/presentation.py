"""Open disposable working copies; never expose immutable revisions to editor saves."""
import asyncio
import json
import re
import shutil
import time
import uuid
import zipfile
from .projects import atomic_json
from .edit_guard import acknowledge_saved, inventory


async def present_revision(session):
    project = session.project
    metadata = project.public()
    if not metadata['head']:
        return
    token = uuid.uuid4().hex
    working = session.state_dir / 'working' / token
    working.mkdir(parents=True, mode=0o700)
    freecad = 'freecad' in session.app['id']
    name = 'model.FCStd' if freecad else 'model.scad'
    target = working / (name if freecad else f"{metadata['head']}_{token[:8]}.scad")
    shutil.copyfile(project.file(metadata['head'], name), target)
    if not freecad:
        # Source builds may import meshes or include sibling files. Open a
        # complete disposable snapshot, never the editable source directory.
        entry = next(r for r in metadata['revisions'] if r['id'] == metadata['head'])
        if 'source.zip' in entry['sha256'] and metadata['design'].get('language') == 'openscad':
            from .source_workspace import SourceWorkspace
            source = working / 'source'
            source.mkdir()
            with zipfile.ZipFile(project.file(metadata['head'], 'source.zip')) as archive:
                for item in archive.infolist():
                    relative = item.filename
                    SourceWorkspace(project).file(relative)  # same path/link policy as the source workspace
                    file = source / relative
                    if item.is_dir():
                        file.mkdir(parents=True, exist_ok=True)
                    else:
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_bytes(archive.read(item))
            target.write_text('include <source/' + metadata['design']['entrypoint'] + '>\n')
        shutil.copyfile(project.file(metadata['head'], 'model.stl'), working / 'model.stl')
    if freecad:
        atomic_json(session.state_dir / 'open-revision.json', {'token': token,
                    'result': metadata['geometry']['result_object']})
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            receipt = session.state_dir / 'opened-revision.json'
            if receipt.exists():
                result = json.loads(receipt.read_text())
                if result.get('token') == token:
                    if result.get('error'):
                        raise ValueError(result['error'])
                    baseline = inventory(result.get('inventory'))
                    if baseline is None:
                        raise ValueError('FreeCAD opened the revision without a complete, timestamped document receipt.')
                    await acknowledge_saved(session, baseline)
                    return
            await asyncio.sleep(.15)
        raise ValueError('Revision saved, but FreeCAD did not acknowledge opening it. Use the artifact download.')
    else:
        # Fixed application shortcut, not model-generated code. Existing tabs stay open.
        def open_file():
            deadline = time.monotonic() + 15
            while not any('.scad' in title and 'OpenSCAD' in title for title in session.screen.window_titles()):
                if time.monotonic() > deadline:
                    raise ValueError('OpenSCAD editor is not ready. Open a blank editor, then reopen the saved project.')
                time.sleep(.15)
            session.screen.key('o', ['ctrl'])
            deadline = time.monotonic() + 5
            # Distribution builds append the application name to Qt dialog titles.
            while not any(re.fullmatch(r'Open(?: File)?(?: [—–-] OpenSCAD)?', title) for title in session.screen.window_titles()):
                if time.monotonic() > deadline:
                    raise ValueError('OpenSCAD did not show its Open File dialog. No filename was typed.')
                time.sleep(.1)
            session.screen.paste_text(str(target))
            session.screen.key('Enter')
            deadline = time.monotonic() + 8
            while not any(target.name in title for title in session.screen.window_titles()):
                if time.monotonic() > deadline:
                    raise ValueError('OpenSCAD did not acknowledge the saved filename. Inspect its dialog before retrying.')
                time.sleep(.15)
            session.screen.key('F5')
            time.sleep(.3)
            # OpenSCAD 2021.01 MainWindow.ui: Diagonal and View All. Ctrl+4 is Top,
            # not fit-to-view. These adjust the camera only, never model geometry.
            session.screen.key('0', ['ctrl'])
            session.screen.key('v', ['ctrl', 'shift'])
        await asyncio.to_thread(open_file)
        await acknowledge_saved(session)
