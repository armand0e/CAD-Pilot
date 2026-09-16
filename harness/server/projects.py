"""Persistent, append-only model revisions and cancellable native builds."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .design import validate_design, scad_source

REFERENCE_NAMES = 'Reference files are plain .step/.stp/.stl/.dxf/.svg/.iges names'


def sniff_reference(content):
    """The CAD file type of downloaded bytes (step, stl, dxf, svg, igs) or None for pages, PDFs and other data."""
    head = content[:65536].lstrip()
    if head.startswith(b'ISO-10303'):
        return 'step'
    if head.startswith(b'%PDF-') or head[:1] == b'<' and b'<svg' not in head:
        return None
    if b'<svg' in head:
        return 'svg'
    if re.match(rb'\s*0\s*\r?\n\s*SECTION', head) or (b'\nSECTION' in head[:4096] and b'\nHEADER' in head[:4096]):
        return 'dxf'
    if len(head) > 80 and head[72:73] == b'S' and head[:72].strip():
        return 'igs'
    if head.startswith(b'solid') and b'facet' in head:
        return 'stl'
    if len(content) >= 84 and not head.startswith(b'solid'):
        import struct
        triangles = struct.unpack('<I', content[80:84])[0]
        if triangles and len(content) == 84 + 50 * triangles:
            return 'stl'
    return None


def reference_name(requested_url, final_url, content):
    """A workspace file name for a downloaded reference: the URL's own name when it has a CAD extension, else sniffed."""
    from urllib.parse import urlsplit
    from .design import REFERENCE_FILE
    kind = sniff_reference(content)
    if kind is None:
        raise ValueError('The URL did not return a STEP, STL, DXF, SVG or IGES file; read pages and PDFs with research instead')
    names = [os.path.basename(urlsplit(u).path) for u in (final_url, requested_url)]
    for name in names:
        if REFERENCE_FILE.fullmatch(name):
            return name
    # Without a CAD extension the requested URL's own name (a document number) beats a download handle.
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '-', (names[1] or names[0] or 'reference').rsplit('.', 1)[0]).strip('.-')[:60] or 'reference'
    return f'{stem}.{kind}'

ROOT = Path(__file__).resolve().parents[1]
FILES = ('design.json', 'geometry.json', 'model.FCStd', 'model.scad', 'model.step', 'model.stl')
OPTIONAL_FILES = ('research.json', 'workspace.json', 'source.zip', 'model.py', 'design-spec.json', 'parts.zip')
# Saved render views: the four defaults plus any the model chose (view-<name>.png).
VIEW_RE = re.compile(r'view-[a-z0-9_-]{1,24}\.png')
REFERENCE_DIR = 'references'


def atomic_json(path, value):
    pending = path.with_suffix('.pending')
    with pending.open('w') as output:
        json.dump(value, output, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


class Project:
    def __init__(self, root, project_id):
        if not re.fullmatch(r'[a-f0-9]{16}', project_id):
            raise ValueError('Invalid project ID')
        self.path = Path(root) / project_id
        if self.path.is_symlink() or not self.path.is_dir():
            raise ValueError('No such project')
        self.id = project_id

    @classmethod
    def create(cls, root, app_id):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        project_id = uuid.uuid4().hex[:16]
        path = root / project_id
        path.mkdir(mode=0o700)
        atomic_json(path / 'project.json', {'id': project_id, 'name': 'Untitled part',
                    'app_id': app_id, 'head': None, 'revisions': [], 'created': time.time()})
        return cls(root, project_id)

    @contextmanager
    def lock(self):
        import fcntl
        with (self.path / '.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def read(self):
        return json.loads((self.path / 'project.json').read_text())

    def file(self, revision, name):
        if (name not in FILES + OPTIONAL_FILES and not VIEW_RE.fullmatch(name)) or not isinstance(revision, str) or not re.fullmatch(r'r[0-9]{4}', revision):
            raise ValueError('Unknown artifact')
        metadata = next((r for r in self.read()['revisions'] if r['id'] == revision), None)
        path = self.path / revision / name
        if not metadata or name not in metadata['sha256'] or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('No such artifact')
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['sha256'][name]:
            raise ValueError('Artifact integrity check failed; revision was modified outside CADPilot')
        return path

    def reference_path(self, name):
        from .design import REFERENCE_FILE
        if not isinstance(name, str) or not REFERENCE_FILE.fullmatch(name):
            raise ValueError(REFERENCE_NAMES)
        path = self.path / REFERENCE_DIR / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Reference {name} is not in this project; import it first')
        return path

    def references(self):
        directory = self.path / REFERENCE_DIR
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir() if p.is_file() and not p.is_symlink())

    def add_reference(self, name, content):
        from .design import REFERENCE_FILE
        if not isinstance(name, str) or not REFERENCE_FILE.fullmatch(name):
            raise ValueError(REFERENCE_NAMES)
        if len(content) > 40 * 1024 * 1024:
            raise ValueError('Reference file exceeds 40 MiB')
        extension = name.rsplit('.', 1)[1].lower()
        kind = sniff_reference(content)
        if extension in ('step', 'stp') and kind != 'step':
            raise ValueError('Not a STEP file (missing ISO-10303 header)')
        if extension in ('dxf', 'svg') and kind != extension:
            raise ValueError(f'Not a {extension.upper()} file')
        directory = self.path / REFERENCE_DIR
        directory.mkdir(exist_ok=True, mode=0o700)
        pending = directory / (name + '.pending')
        pending.write_bytes(content)
        pending.replace(directory / name)
        return name

    def current_design(self):
        head = self.read()['head']
        return json.loads(self.file(head, 'design.json').read_text()) if head else None

    def public(self):
        metadata = self.read()
        head = metadata['head']
        entry = next((r for r in metadata['revisions'] if r['id'] == head), {})
        research = json.loads(self.file(head, 'research.json').read_text()) if 'research.json' in entry.get('sha256', {}) else None
        workspace = json.loads(self.file(head, 'workspace.json').read_text()) if 'workspace.json' in entry.get('sha256', {}) else None
        return metadata | {'design': self.current_design(), 'research': research, 'workspace': workspace, 'geometry':
            json.loads(self.file(head, 'geometry.json').read_text()) if head else None}

    def record_attempt(self, raw, error, step, attempt, parent):
        """Bounded diagnostic records, separate from immutable successful revisions."""
        directory = self.path / 'attempts'
        if directory.is_symlink():
            raise OSError('Diagnostic directory is a symlink')
        directory.mkdir(exist_ok=True, mode=0o700)
        name = 'attempt_' + uuid.uuid4().hex + '.json'
        atomic_json(directory / name, {'created': time.time(), 'parent': parent, 'step': step,
                    'attempt': attempt, 'raw_recipe': raw[:48000], 'error': error[:8000], 'committed': False})
        owned = [p for p in directory.iterdir() if re.fullmatch(r'attempt_[a-f0-9]{32}\.json', p.name) and not p.is_symlink()]
        for path in sorted(owned, key=lambda p: p.stat().st_mtime, reverse=True)[20:]:
            path.unlink()

    async def prepare(self, design):
        """No project mutation. Caller owns returned staging dir; cancellation cleans it."""
        design = validate_design(design, allow_unused=True)
        if shutil.disk_usage(self.path).free < 5 * 1024**3:
            raise ValueError('Less than 5 GiB free; native build paused to protect existing work')
        stage = Path(tempfile.mkdtemp(prefix='.build-', dir=self.path))
        try:
            atomic_json(stage / 'design.json', design)
            (stage / 'model.scad').write_text(scad_source(design))
            needed = {f['options']['file'] for f in design['features'] if f['kind'] == 'reference'}
            if needed:
                (stage / REFERENCE_DIR).mkdir()
                for name in needed:
                    shutil.copyfile(self.reference_path(name), stage / REFERENCE_DIR / name)
            await run_worker(stage)
            for name in FILES:
                path = stage / name
                if not path.is_file() or path.stat().st_size < 2 or path.stat().st_size > 64 * 1024**2:
                    raise ValueError(f'Native export missing or oversized: {name}')
            report = json.loads((stage / 'geometry.json').read_text())
            final = next(f for f in design['features'] if f['id'] == design['result'])
            count = len(final['inputs']) if final['kind'] == 'parts' else 1
            valid = (report.get('valid_solid') is True if count == 1 else
                     report.get('valid_geometry') is True and report.get('valid_solid') is False and
                     len(report.get('parts', [])) == count and all(p.get('solid_count') == 1 for p in report['parts']))
            if not valid or report.get('solid_count') != count:
                raise ValueError('Native validation failed')
            return stage
        except BaseException:
            shutil.rmtree(stage)
            raise

    def commit(self, stage, expected_head, *, restored_from=None):
        """Short atomic commit, after async generation, before any further await."""
        stage = Path(stage)
        if stage.parent != self.path or not stage.name.startswith('.build-') or stage.is_symlink():
            raise ValueError('Not an owned staging directory')
        with self.lock():
            metadata = self.read()
            if metadata['head'] != expected_head:
                raise ValueError('Project changed during generation. Re-read the current revision before editing.')
            if len(metadata['revisions']) >= 9999:
                raise ValueError('Project reached 9999 revisions; create a new project to continue')
            # Orphaned revision directories from an interrupted commit are never overwritten.
            index = max([int(p.name[1:]) for p in self.path.iterdir() if re.fullmatch(r'r[0-9]{4}', p.name)] or [0]) + 1
            revision = f'r{index:04d}'
            design = json.loads((stage / 'design.json').read_text())
            entry = {'id': revision, 'created': time.time(), 'name': design['name'],
                     'parent': expected_head, 'restored_from': restored_from,
                     'compiler': 'native-csg-v2',
                     'compiler_sha256': {name: hashlib.sha256((ROOT / 'server' / name).read_bytes()).hexdigest()
                                         for name in ('design.py', 'cad_worker.py', 'stl_audit.py')},
                     'sha256': {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                                for name in FILES + tuple(n for n in OPTIONAL_FILES if (stage / n).is_file())
                                + tuple(sorted(p.name for p in stage.glob('view-*.png') if VIEW_RE.fullmatch(p.name)))}}
            if (stage / 'workspace.json').is_file():
                entry['operation_contract'] = 'native-operations-v1'
                entry['operation_compiler_sha256'] = hashlib.sha256((ROOT / 'server/operations.py').read_bytes()).hexdigest()
            if design.get('format') == 'source-v1':
                entry['compiler'] = 'source-cad-v1'
                entry['compiler_sha256'].update({name: hashlib.sha256((ROOT / 'server' / name).read_bytes()).hexdigest()
                                                for name in ('source_program.py', 'source_kernel.py', 'cad_paths.py', 'svg_path.py')})
            if restored_from:
                original = next(r for r in metadata['revisions'] if r['id'] == restored_from)
                entry['compiler'] = original.get('compiler', 'legacy-native-csg')
                entry['compiler_sha256'] = original.get('compiler_sha256', {})
                if 'operation_contract' in original:
                    entry['operation_contract'] = original['operation_contract']
                    entry['operation_compiler_sha256'] = original['operation_compiler_sha256']
            stage.rename(self.path / revision)
            metadata.update(head=revision, name=design['name'])
            metadata['revisions'].append(entry)
            atomic_json(self.path / 'project.json', metadata)
        return self.public()

    def restore(self, revision, expected_head):
        stage = Path(tempfile.mkdtemp(prefix='.build-', dir=self.path))
        try:
            entry = next((r for r in self.read()['revisions'] if r['id'] == revision), {})
            for name in entry.get('sha256', {}):
                shutil.copyfile(self.file(revision, name), stage / name)
            return self.commit(stage, expected_head, restored_from=revision)
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def build_error(log, exit_code=None):
    """The kernel's own message, not its traceback; the full log stays in compiler.log."""
    lines = [line for line in log.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        match = re.match(r'^(?:\w+\.)?(?:ValueError|TypeError|RuntimeError|Exception|Part\.OCCError|\w+Error): (.*)$', line)
        if match:
            return match.group(1)[:3000]
    if exit_code is not None and (exit_code < 0 or exit_code > 128) and not lines:
        signal_name = {-11: 'segmentation fault', 139: 'segmentation fault', -6: 'abort', 134: 'abort', -9: 'killed', 137: 'killed (out of memory?)'}.get(exit_code, f'exit code {exit_code}')
        return (f'The CAD kernel process crashed ({signal_name}) while running the source, without a Python error. '
                'Typical causes: a fillet or chamfer that cannot be built at that radius (especially on concave or short edges), '
                'a boolean between invalid or coincident shapes, or an offset of a spline surface. Use a smaller radius, build the '
                'profile with the arc drawn in (cad_paths), or a different construction order.')
    return ' '.join(lines[-3:])[-3000:] if lines else 'no compiler output'


async def run_worker(stage):
    """No host home, network, display or project files in the compiler sandbox."""
    runtime = ROOT / 'apps/freecad-extracted'
    if not (runtime / 'usr/bin/python').exists() or not shutil.which('bwrap'):
        raise ValueError('Native tools require the bundled FreeCAD runtime and bubblewrap')
    command = ['prlimit', '--as=8589934592', '--cpu=90', '--fsize=67108864', '--',
               'bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
               '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--ro-bind', '/etc/passwd', '/etc/passwd', '--ro-bind', '/etc/group', '/etc/group',
               '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LANG', 'C.UTF-8',
               '--setenv', 'QT_QPA_PLATFORM', 'offscreen', '--setenv', 'OMP_NUM_THREADS', '2',
               # numpy's OpenBLAS spins forever when its thread buffers cannot be reserved under the
               # address-space limit; a single BLAS thread needs none of that.
               '--setenv', 'OPENBLAS_NUM_THREADS', '1', '--setenv', 'OPENBLAS_MAIN_FREE', '1',
               '--setenv', 'PYTHONHOME', '/opt/cad/usr', '--setenv', 'PYTHONPATH', '/opt/cad/usr/lib:/',
               '--ro-bind', str(runtime), '/opt/cad', '--bind', str(stage), '/work', '--chdir', '/work',
               '--ro-bind', str(ROOT / 'server/design.py'), '/design.py',
               '--ro-bind', str(ROOT / 'server/cad_worker.py'), '/worker.py',
               '--ro-bind', str(ROOT / 'server/stl_audit.py'), '/stl_audit.py',
               '/opt/cad/usr/bin/python', '/worker.py']
    with (stage / 'compiler.log').open('wb') as log:
        proc = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log, start_new_session=True)
        try:
            await asyncio.wait_for(proc.wait(), 100)
            if proc.returncode:
                raise ValueError('Native CAD build failed (previous revision preserved): ' +
                                 build_error((stage / 'compiler.log').read_text(errors='replace')))
        finally:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
