"""Run inside the CAD container: real compiler, viewers, browser, PDF and search."""
import asyncio
import math
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.detect import detect_apps
from server.native import read_native_state
from server.operations import candidate, workspace
from server.presentation import present_revision
from server.projects import Project, atomic_json
from server.research import ResearchTool
from server.sessions import SessionManager
from server.browser_research import local_page
from server.source_workspace import SourceWorkspace


async def check_source_workspace(root):
    project = Project.create(root / 'projects', 'appimage-freecad')
    work = SourceWorkspace(project)
    work.ensure()
    assert 'cad_build' in work.file('CAD_GUIDE.md').read_text()
    await work.fs({'action': 'write', 'path': 'model.py', 'content':
        'from cad_paths import extrude\nparts = {"Housing": extrude("M0 0 H40 Q44 0 44 4 V16 Q44 20 40 20 H0 Z", 6)}\n'})
    stage = await work.prepare('model.py', None)
    saved = project.commit(stage, None)
    work.built(saved)
    assert saved['geometry']['valid_solid']
    assert saved['geometry']['solid_count'] == 1
    assert all(abs(a - b) < .001 for a, b in zip(saved['geometry']['bounds_mm'], [44, 20, 6]))
    for name in ('source.zip', 'model.FCStd', 'model.step', 'model.stl', 'view-iso.png'):
        assert project.file(saved['head'], name).stat().st_size > 100
    print('PASS: installed workspace guide, source Python, curved path, build and saved exports', flush=True)


async def main():
    with tempfile.TemporaryDirectory(prefix='cadpilot-check-') as directory:
        root = Path(directory)
        await check_source_workspace(root)
        project = Project.create(root / 'projects', 'appimage-freecad')
        saved = workspace()
        for tool, arguments in [
            ('create_body', {'id': 'plate', 'name': 'Container check', 'kind': 'box', 'dimensions': ['60', '40', '8'],
                             'at': ['0', '0', '0'], 'anchor': 'corner', 'axis': 'z', 'parameters': []}),
            ('hole', {'body': 'plate', 'face': 'top', 'u': '8', 'v': '8', 'diameter': '5', 'depth': 'through', 'parameters': []}),
        ]:
            saved, design, _ = candidate(saved, {'tool': tool, 'arguments': arguments, 'plan': 'Container smoke check'})
        stage = await project.prepare(design)
        atomic_json(stage / 'workspace.json', saved)
        result = project.commit(stage, None)
        assert abs(result['geometry']['volume_mm3'] - (60 * 40 * 8 - math.pi * 2.5**2 * 8)) < .00001
        for name in ('model.FCStd', 'model.step', 'model.stl', 'model.scad', 'view-iso.png'):
            assert project.file(result['head'], name).stat().st_size > 100
        print('PASS: FreeCAD solid, bore volume, STEP/STL/native exports and rendered views', flush=True)
        apps = {app.id: app.to_json() for app in detect_apps()}
        manager = SessionManager(state_root=root / 'sessions')
        for app_id in ('appimage-freecad', 'appimage-openscad'):
            session = await asyncio.to_thread(manager.create, apps[app_id] | {'native_project': True})
            try:
                session.project = project
                # First startup can take longer than reopening a document.
                for _ in range(100):
                    if app_id == 'appimage-freecad':
                        ready = read_native_state(session.state_dir)
                    else:
                        ready = any('.scad' in title for title in session.screen.window_titles())
                    if ready:
                        break
                    await asyncio.sleep(.2)
                await present_revision(session)
                assert session.app_proc.poll() is None
                assert len(session.screen.capture()) > 5000
                print(f'PASS: {app_id} desktop, document open, frame capture', flush=True)
            finally:
                await asyncio.to_thread(manager.close, session.id)
        async with local_page() as page:
            await page.set_content('<main><h1>Sandboxed browser</h1></main>')
            assert await page.locator('h1').inner_text() == 'Sandboxed browser'
        print('PASS: Chromium sandbox and public browser proxy', flush=True)
        from server.research import pdf_text, pdf_page_images
        from PIL import Image
        import io
        pdf = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(pdf, 'PDF')
        await pdf_text(pdf.getvalue())
        assert len(await pdf_page_images(pdf.getvalue(), pages=1)) == 1
        print('PASS: networkless PDF text and page-image workers', flush=True)
        result = await ResearchTool({'enabled': True, 'searxng_url': 'http://searxng:8080'}).search('FreeCAD Part cylinder documentation')
        assert result['provider'] == 'searxng' and result['sources']
        print('PASS: live SearXNG JSON search', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
