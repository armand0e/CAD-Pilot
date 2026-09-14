"""Project files, versioned specifications and immutable source build snapshots."""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
import uuid
import zipfile

from .projects import ROOT, atomic_json, build_error
from .cad_sandbox import execute
from .specification import ROWS, merge_patch, check_verification

MAX_FILE = 64 * 1024 * 1024
MAX_TEXT = 1024 * 1024
RESERVED = {'design-spec.json', 'CAD_GUIDE.md', 'cad_paths.py'}
CONTEXT_DIR = '.cadpilot-context'
# Required software resources must not live in the persisted knowledge volume:
# an existing Docker volume hides files added to that directory in a new image.
SERVER = Path(__file__).resolve().parent
SUPPLIED_FILES = {'CAD_GUIDE.md': SERVER / 'guides/source-workspace.md', 'cad_paths.py': SERVER / 'cad_paths.py'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SourceWorkspace:
    def __init__(self, project):
        self.project = project
        self.path = project.path / 'source'
        self.meta = project.path / 'source-state.json'

    def file(self, name):
        if not isinstance(name, str):
            raise ValueError('File path must be a string')
        name = name.removeprefix('/work/')
        if name == '/work':
            name = '.'
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or '\x00' in name:
            raise ValueError('Files must stay inside /work')
        path = self.path
        for part in ('.', *relative.parts):
            path = path / part
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError('Workspace links are not allowed')
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise ValueError('Only regular files and directories are supported')
        return path

    def files(self):
        result, size = [], 0
        if not self.path.exists():
            return result
        self.file('.')
        for root, dirs, names in os.walk(self.path, followlinks=False):
            if Path(root) == self.path and CONTEXT_DIR in dirs:
                dirs.remove(CONTEXT_DIR)  # Regenerable tool records are not modeling source.
            for name in dirs + names:
                path = self.file((Path(root) / name).relative_to(self.path).as_posix())
                if path.is_dir():
                    continue
                length = path.stat().st_size
                size += length
                if length > MAX_FILE or size > 256 * 1024 * 1024 or len(result) >= 1024:
                    raise ValueError('Workspace exceeds 64 MiB per file, 256 MiB total or 1024 files')
                result.append({'path': path.relative_to(self.path).as_posix(), 'bytes': length,
                               'sha256': digest(path.read_bytes())})
        return sorted(result, key=lambda f: f['path'])

    def state(self):
        return json.loads(self.meta.read_text()) if self.meta.exists() else {}

    def ensure(self):
        # Check the installed resources before creating or seeding project files.
        supplied = {name: source.read_bytes() for name, source in SUPPLIED_FILES.items()}
        self.file('.')
        with self.project.lock():
            self.path.mkdir(exist_ok=True, mode=0o700)
            if not self.meta.exists():
                self.seed(self.project.read()['head'])
            for name, data in supplied.items():
                path = self.file(name)
                if path.is_file() and path.read_bytes() == data:
                    continue
                fd, pending = tempfile.mkstemp(dir=self.path, prefix='.supplied-')
                try:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(data)
                    os.replace(pending, path)
                finally:
                    Path(pending).unlink(missing_ok=True)
            if not (self.project.path / 'spec-state.json').exists():
                initial = {'version': 0, 'objective': '', 'coordinates': 'Millimetres; right-handed x/y/z. Image directions not yet established.',
                           'requirements': [], 'decisions': [], 'references': [], 'open_questions': [], 'addressed_inputs': []}
                atomic_json(self.project.path / 'spec-state.json', initial)
                atomic_json(self.file('design-spec.json'), initial)
        # New uploads become available without replacing any edited source file.
        for name in self.project.references():
            target = self.file('references/' + name)
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copyfile(self.project.reference_path(name), target)

    def seed(self, head):
        source_head = False
        if head:
            entry = next(r for r in self.project.read()['revisions'] if r['id'] == head)
            if 'source.zip' in entry['sha256']:
                with zipfile.ZipFile(self.project.file(head, 'source.zip')) as archive:
                    total = 0
                    for info in archive.infolist():
                        total += info.file_size
                        if info.file_size > MAX_FILE or total > 256 * 1024 * 1024 or len(archive.infolist()) > 1024:
                            raise ValueError('Source snapshot exceeds workspace limits')
                        target = self.file(info.filename)
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.read(info))
                source_head = True
            else:
                shutil.copyfile(self.project.file(head, 'model.FCStd'), self.file('base.FCStd'))
                geometry = json.loads(self.project.file(head, 'geometry.json').read_text())
                source = ('import FreeCAD as App\nimport Part\n\ndoc = App.openDocument("base.FCStd")\n'
                          'doc.recompute()\nparts = {"Model": doc.getObject(' + repr(geometry.get('result_object', 'Result')) + ').Shape.copy()}\n')
                self.file('model.py').write_text(source)
                shutil.copyfile(self.project.file(head, 'model.scad'), self.file('model.scad'))
        else:
            self.file('model.py').write_text('import FreeCAD as App\nimport Part\n\nlength, width, height = 20, 10, 5\nparts = {"Model": Part.makeBox(length, width, height)}\n')
            self.file('model.scad').write_text('length=20; width=10; height=5;\ncube([length,width,height]);\n')
        atomic_json(self.meta, {'base_head': head, 'source_mode': source_head,
                               'baseline': {f['path']: f['sha256'] for f in self.files() if f['path'] not in RESERVED}})

    def dirty(self):
        return {f['path']: f['sha256'] for f in self.files() if f['path'] not in RESERVED} != self.state().get('baseline', {})

    def checkout(self, revision, discard_changes=False):
        self.ensure()
        if self.dirty() and not discard_changes:
            raise ValueError('Unsaved source edits exist. Read them before explicitly discarding changes.')
        if revision != self.project.read()['head']:
            # Restoring geometry is an explicit existing UI operation; source cannot silently rebase onto unrelated geometry.
            raise ValueError('Checkout must match the current saved revision. Restore that revision first to use an older base.')
        for item in list(self.path.iterdir()):
            if item.name in RESERVED:
                continue
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        self.seed(revision)
        # Preserve the current spec: old source recovery must not erase newer user decisions.
        atomic_json(self.file('design-spec.json'), self.spec())
        return self.describe()

    def inputs(self):
        path = self.project.path / 'input-evidence.json'
        return json.loads(path.read_text()) if path.exists() else []

    def record_inputs(self, events):
        self.ensure()
        with self.project.lock():
            records = self.inputs()
            known = {r['id'] for r in records}
            count = len(records)
            for e in events:
                if e.get('t') not in ('user', 'answer'):
                    continue
                text = e.get('summary') or e.get('text', '')
                if not text:
                    continue
                identity = 'input:' + str(e.get('event_id') or digest(json.dumps(e, sort_keys=True).encode()))
                if identity not in known:
                    records.append({'id': identity, 'text': text, 'kind': e['t'], 'question_id': e.get('question_id'),
                                    'created': e.get('ts'), 'attachments': e.get('attachments', [])})
                    known.add(identity)
            if len(records) != count:
                atomic_json(self.project.path / 'input-evidence.json', records)
        value = self.spec()
        if records and not value['objective'] and not value['requirements']:
            first = records[0]
            value.update(objective=first['text'], requirements=[{'id': 'initial-request', 'text': first['text'],
                         'origin': 'user', 'evidence': [first['id']], 'status': 'open', 'features': []}])
            self.update_spec(value, value['version'])

    def evidence(self):
        directory = self.project.path / 'inspection-evidence'
        return [json.loads(p.read_text()) for p in sorted(directory.glob('*.json'))] if directory.exists() else []

    def sources(self):
        path = self.project.path / 'source-evidence.json'
        known = json.loads(path.read_text()) if path.is_file() else {}
        conversation = self.project.path / 'conversation.json'
        saved = json.loads(conversation.read_text()) if conversation.is_file() else {}
        updated = dict(known)
        for item in saved.get('research', {}).get('sources', []):
            if item.get('kind') in ('page', 'pdf'):
                updated[item['id']] = item
        if updated != known:
            atomic_json(path, updated)
        return updated

    def save_evidence(self, data):
        directory = self.project.path / 'inspection-evidence'
        directory.mkdir(exist_ok=True, mode=0o700)
        value = {'id': 'measure:' + uuid.uuid4().hex, 'created': time.time(), **data}
        atomic_json(directory / (value['id'].split(':')[1] + '.json'), value)
        return value

    def spec(self):
        return json.loads((self.project.path / 'spec-state.json').read_text())

    def validate_spec(self, value, current=None):
        if not isinstance(value, dict) or not isinstance(value.get('objective'), str) or not isinstance(value.get('coordinates'), str):
            raise ValueError('Specification needs objective and coordinates strings')
        from .attachments import saved_image_ids
        available = {r['id'] for r in self.inputs()} | {r['id'] for r in self.evidence()}
        # Source/page IDs and actual image IDs, never a search-result-only claim.
        available |= set(self.sources())
        available |= set(saved_image_ids(self.project.path))
        evidence = {r['id']: r for r in self.evidence()}
        current = self.spec() if current is None else current
        old_rows = {r['id']: r for key in ROWS for r in current[key]}
        seen = set()
        verification_errors = []
        for key in ('requirements', 'decisions', 'references', 'open_questions', 'addressed_inputs'):
            if not isinstance(value.get(key), list):
                raise ValueError(f'Specification {key} must be an array')
        for key in ('open_questions', 'addressed_inputs'):
            if any(not isinstance(i, str) for i in value[key]):
                raise ValueError(f'Specification {key} entries must be strings')
        for row in value['requirements'] + value['decisions'] + value['references']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen or not isinstance(row.get('text'), str):
                raise ValueError('Requirements, decisions and reference observations need unique IDs and text')
            seen.add(row['id'])
            if row.get('origin') not in ('user', 'sourced', 'assumed'):
                raise ValueError('Record origin as user, sourced or assumed')
            ids = row.get('evidence', [])
            if not isinstance(ids, list) or any(not isinstance(i, str) or i not in available for i in ids):
                raise ValueError(f'Unknown evidence for {row["id"]}; read spec_read/inspect for actual IDs')
            if row['origin'] == 'user' and not any(i.startswith('input:') for i in ids):
                raise ValueError('User decisions require an actual user input evidence ID')
            if row['origin'] == 'sourced' and not any(not i.startswith(('input:', 'measure:')) for i in ids):
                raise ValueError('Sourced requirements require an opened page or saved image ID')
            if row.get('status', 'open') not in ('open', 'implemented', 'verified', 'retired'):
                raise ValueError('Requirement status is open, implemented, verified or retired')
            if not isinstance(row.get('features', []), list) or any(not isinstance(f, str) for f in row.get('features', [])):
                raise ValueError('Feature links must be an array of body/face names')
            if 'retirement' in row and not isinstance(row['retirement'], dict):
                raise ValueError('Retirement needs a reason and user evidence')
            if 'retirement' in row and (not isinstance(row['retirement'].get('evidence', []), list) or
                    any(not isinstance(i, str) for i in row['retirement'].get('evidence', []))):
                raise ValueError('Retirement evidence must be user input IDs')
            if row.get('status') == 'retired':
                retirement = row.get('retirement', {})
                refs = retirement.get('evidence', []) if isinstance(retirement, dict) else []
                if not isinstance(retirement, dict) or not isinstance(retirement.get('reason'), str) or not retirement['reason'].strip() or not isinstance(refs, list) or not refs or any(not isinstance(i, str) or not i.startswith('input:') or i not in available for i in refs):
                    raise ValueError('Retiring a requirement needs a reason and actual user input evidence IDs')
            if row.get('status') == 'verified' and row != old_rows.get(row['id']):
                check = row.get('verification')
                if isinstance(check, dict) and check.get('kind') == 'task':
                    target = self.file(check.get('file', ''))
                    if target.is_file():
                        check['sha256'] = digest(target.read_bytes())
                try:
                    verified = check_verification(row, evidence, self.project.read()['head'], self.file)
                    # The check already names its evidence/geometry. Store those
                    # links instead of making the model repeat them perfectly.
                    if verified['kind'] != 'task':
                        row['evidence'] = list(dict.fromkeys(ids + [check['evidence']]))
                    if verified.get('subjects') and not row.get('features'):
                        row['features'] = verified['subjects']
                except ValueError as error:
                    verification_errors.append(row['id'] + ': ' + str(error))
        if verification_errors:
            raise ValueError('Verification checks need correction. ' + ' | '.join(verification_errors))
        missing = set(old_rows) - seen
        if missing:
            raise ValueError('Do not remove specification IDs: ' + ', '.join(sorted(missing)) + '. Keep them with status=retired, a reason and user evidence.')
        if any(i not in {r['id'] for r in self.inputs()} for i in value['addressed_inputs']):
            raise ValueError('addressed_inputs must reference recorded user input IDs')
        linked = {i for key in ROWS for row in value[key] for i in row.get('evidence', [])}
        linked |= {i for key in ROWS for row in current[key] for i in row.get('evidence', [])}
        linked |= {i for key in ROWS for row in value[key] for i in row.get('retirement', {}).get('evidence', [])}
        if set(value['addressed_inputs']) - set(current['addressed_inputs']) - linked:
            raise ValueError('Link newly addressed inputs to a requirement, decision, reference or retirement first')
        if len(json.dumps(value).encode()) > MAX_TEXT:
            raise ValueError('Specification exceeds 1 MiB')
        return value

    def update_spec(self, value, expected_version, *, patch=False):
        with self.project.lock():
            current = self.spec()
            if type(expected_version) is not int or expected_version != current['version']:
                raise ValueError('Specification changed; read it again before editing')
            if patch:
                value = merge_patch(current, value)
            self.validate_spec(value, current)
            if value == current:
                return current
            value = {**value, 'version': current['version'] + 1}
            directory = self.project.path / 'spec-history'
            directory.mkdir(exist_ok=True, mode=0o700)
            atomic_json(directory / f'{value["version"]:06d}.json', value)
            atomic_json(self.project.path / 'spec-state.json', value)
            atomic_json(self.file('design-spec.json'), value)
        return value

    def sync_spec(self):
        value = json.loads(self.file('design-spec.json').read_text())
        if value != self.spec():
            return self.update_spec(value, value.get('version'))
        return value

    def spec_context(self, input_offset=0, evidence_offset=0, limit=20):
        if any(type(v) is not int or v < 0 for v in (input_offset, evidence_offset)) or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Evidence pagination uses nonnegative offsets and limit 1..50')
        value = self.spec()
        inputs, evidence = self.inputs(), self.evidence()
        pending = [r for r in inputs if r['id'] not in value['addressed_inputs']]
        summaries = [{k:v for k,v in e.items() if k in ('id','revision','query','object','a','b','minimum_distance_mm',
                      'intersection_volume_mm3','axis','at_mm','image')} for e in evidence]
        specification = value if len(json.dumps(value)) <= 24000 else {
            'version': value['version'], 'objective': value['objective'][:1200], 'path': '/work/design-spec.json',
            'notice': 'Read the complete specification with Pi read (offset/limit); it is too large for this overview.'}
        checks = {}
        evidence_by_id, head = {e['id']: e for e in evidence}, self.project.read()['head']
        for key in ROWS:
            for row in value[key]:
                if row.get('status') == 'verified':
                    try:
                        checks[row['id']] = {'valid': True, **check_verification(row, evidence_by_id, head, self.file)}
                    except ValueError as error:
                        checks[row['id']] = {'valid': False, 'reason': str(error)}
        return self.context_result('spec-context', {'specification': specification, 'pending_inputs': pending[-limit:], 'pending_input_count': len(pending),
                'input_evidence': inputs[input_offset:input_offset+limit], 'inspection_evidence': summaries[evidence_offset:evidence_offset+limit],
                'input_count': len(inputs), 'evidence_count': len(evidence),
                'next_input_offset': input_offset+limit if input_offset+limit < len(inputs) else None,
                'next_evidence_offset': evidence_offset+limit if evidence_offset+limit < len(evidence) else None,
                'source_ids': [{'id':s['id'], 'title':s.get('title'), 'url':s.get('url')} for s in self.sources().values()],
                'verification_notice': 'Measurement and visual checks belong to a revision; task checks belong to a file hash. Legacy unchecked claims need re-verification.',
                'verification_checks': checks, 'stale_requirements': [key for key, check in checks.items() if not check['valid']]})

    def context_result(self, name, value, limit=24000):
        """Bound model-facing JSON, keeping complete records readable with Pi."""
        if len(json.dumps(value)) <= limit:
            return value
        path = self.file(CONTEXT_DIR + '/' + name + '.json')
        path.parent.mkdir(exist_ok=True)
        data = json.dumps(value, indent=2, ensure_ascii=True)
        if not path.exists() or path.read_text() != data:
            # This is a disposable projection, never authoritative evidence.
            fd, pending = tempfile.mkstemp(dir=path.parent, prefix='.context-')
            try:
                with os.fdopen(fd, 'w') as output:
                    output.write(data)
                os.replace(pending, path)
            finally:
                Path(pending).unlink(missing_ok=True)
        def preview(item, width, depth=0):
            if isinstance(item, str):
                return item if len(item) <= width else item[:width] + '… [preview]'
            if isinstance(item, (list, dict)) and depth > 5:
                return {'notice': 'Read context_file for this collection', 'count': len(item)}
            if isinstance(item, list):
                return [preview(x, width, depth+1) for x in item[:5]] + ([{'omitted_items': len(item)-5}] if len(item)>5 else [])
            if isinstance(item, dict):
                return {k: preview(v, width, depth+1) for k,v in list(item.items())[:40]}
            return item
        for width in (800, 200, 40):
            result = {'preview': preview(value, width), 'context_file': '/work/' + str(path.relative_to(self.path)),
                      'notice': 'Overview truncated. Read context_file with Pi read offset/limit for complete records; do not treat abbreviated values as exact evidence.'}
            if len(json.dumps(result)) <= limit:
                return result
        return {k:v for k,v in result.items() if k != 'preview'}

    def describe(self):
        self.ensure()
        state, files = self.state(), self.files()
        return {'root': '/work', 'base_head': state.get('base_head'), 'source_mode': state.get('source_mode', False),
                'files': files, 'dirty': {f['path']: f['sha256'] for f in files if f['path'] not in RESERVED} != state.get('baseline', {}),
                'current_head': self.project.read()['head'], 'specification_path': '/work/design-spec.json', 'specification_version': self.spec()['version'],
                'help': 'Read CAD_GUIDE.md; edit model.py/model.scad, then cad_build. Use cad_checkout after restoring or changing the project base.'}

    async def fs(self, args):
        self.ensure()
        action = args['action']
        if action == 'bash':
            timeout = args.get('timeout')
            if timeout is not None and (not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400):
                raise ValueError('timeout must be positive seconds (up to one day); omit for no wall-clock limit')
            result = await execute(self.path, ['/bin/bash', '-lc', args['command']], timeout=timeout)
            self.files()
            try:
                self.sync_spec()
            except ValueError as error:
                result['output'] += '\nSpecification draft was not accepted: ' + str(error)
                result['exitCode'] = result['exitCode'] or 1
            return result
        path = self.file(args.get('path', '.'))
        if action == 'mkdir':
            path.mkdir(parents=True, exist_ok=True)
        elif action == 'access':
            if not path.is_file():
                raise FileNotFoundError('No such workspace file')
        elif action == 'read':
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('File is missing or exceeds the 8 MiB read limit; use cad_inspect for large CAD artifacts')
            return {'data': base64.b64encode(path.read_bytes()).decode()}
        elif action == 'write':
            data = args['content'].encode()
            if len(data) > MAX_TEXT:
                raise ValueError('Text files are limited to 1 MiB')
            if path.name in ('CAD_GUIDE.md', 'cad_paths.py') and path.parent == self.path:
                raise ValueError(path.name + ' is supplied by CADPilot')
            if path == self.file('design-spec.json'):
                value = json.loads(data)
                return self.update_spec(value, value.get('version'))
            if args.get('expected_sha256') is not None:
                current = digest(path.read_bytes()) if path.is_file() else ''
                if current != args['expected_sha256']:
                    raise ValueError('File changed; reload before saving')
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, pending = tempfile.mkstemp(dir=path.parent, prefix='.edit-')
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(data)
                os.replace(pending, path)
            finally:
                Path(pending).unlink(missing_ok=True)
        else:
            raise ValueError('Unknown workspace operation')
        return {'ok': True}

    async def prepare(self, entrypoint, expected_head):
        self.ensure()
        if self.state().get('base_head') != expected_head:
            if self.dirty():
                raise ValueError('The saved model changed after these source edits. Inspect both before cad_checkout.')
            self.checkout(expected_head)
        entry = self.file(entrypoint)
        if entry.suffix not in ('.py', '.scad') or not entry.is_file():
            raise ValueError('Entrypoint must be an existing .py or .scad workspace file')
        self.sync_spec()
        files = self.files()
        if shutil.disk_usage(self.path).free < 2 * 1024**3:
            raise ValueError('Less than 2 GiB free; source build cannot start')
        stage = Path(tempfile.mkdtemp(prefix='.build-', dir=self.project.path))
        scratch = Path(tempfile.mkdtemp(prefix='.program-', dir=self.project.path))
        result = None
        try:
            with zipfile.ZipFile(stage / 'source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    name = item['path']
                    data = self.file(name).read_bytes()
                    if digest(data) != item['sha256']:
                        raise ValueError('Source changed while snapshotting; retry the build')
                    archive.writestr(name, data)
                    target = scratch / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            relative = entry.relative_to(self.path).as_posix()
            if entry.suffix == '.py':
                result = await execute(scratch, ['/opt/cad/usr/bin/python', '/source_program.py', relative], helpers=('source_program.py',))
                if result['exitCode']:
                    raise ValueError(build_error(result['output']))
                manifest = scratch / 'build-parts.json'
                if manifest.is_symlink() or manifest.stat().st_size > MAX_TEXT:
                    raise ValueError('Invalid parts manifest')
                rows = json.loads(manifest.read_text())
                if not isinstance(rows, list) or not 1 <= len(rows) <= 1024:
                    raise ValueError('No exported parts, or more than 1024 parts')
                for row in rows:
                    if not isinstance(row, dict) or not re.fullmatch(r'part-[0-9]+\.brep', row.get('file', '')) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', row.get('name', '')):
                        raise ValueError('Part names must be unique identifiers, e.g. Case or Lid')
                    path = scratch / row['file']
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                        raise ValueError('Invalid exported BREP')
                    shutil.copyfile(path, stage / row['file'])
                if len({r['name'] for r in rows}) != len(rows):
                    raise ValueError('Duplicate part names')
                atomic_json(stage / 'build-parts.json', rows)
                with zipfile.ZipFile(stage / 'source.zip') as archive:
                    (stage / 'model.py').write_bytes(archive.read(relative))
                (stage / 'model.scad').write_text('// FreeCAD Python build; faceted preview only.\nimport("model.stl");\n')
            else:
                scad = '/usr/bin/openscad' if Path('/usr/bin/openscad').is_file() else '/opt/scad/AppRun'
                result = await execute(scratch, [scad, '-o', '/work/source.stl', relative])
                if result['exitCode']:
                    raise ValueError(build_error(result['output']))
                path = scratch / 'source.stl'
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                    raise ValueError('Invalid OpenSCAD STL output')
                shutil.copyfile(path, stage / 'source.stl')
                # Keep includes/imports portable in the source archive; the GUI gets the full source directory.
                with zipfile.ZipFile(stage / 'source.zip') as archive:
                    (stage / 'model.scad').write_bytes(archive.read(relative))
            (stage / 'program.log').write_text(result['output'])
            design = {'name': self.project.read()['name'] if expected_head else entry.stem,
                      'format': 'source-v1', 'language': 'freecad-python' if entry.suffix == '.py' else 'openscad',
                      'entrypoint': relative, 'parameters': [], 'features': [], 'source_files': files}
            atomic_json(stage / 'design.json', design)
            atomic_json(stage / 'design-spec.json', self.spec())
            result = await execute(stage, ['/opt/cad/usr/bin/python', '/source_kernel.py', 'build'],
                                   helpers=('source_kernel.py', 'cad_worker.py', 'design.py', 'stl_audit.py'))
            (stage / 'compiler.log').write_text(result['output'])
            if result['exitCode']:
                raise ValueError(build_error(result['output']))
            from .projects import FILES
            for name in FILES:
                path = stage / name
                if path.is_symlink() or not path.is_file() or not 2 <= path.stat().st_size <= MAX_FILE:
                    raise ValueError('Missing or oversized source build artifact: ' + name)
            return stage
        except BaseException as error:
            if isinstance(error, Exception):
                with contextlib.suppress(OSError):
                    self.project.record_attempt(json.dumps({'entrypoint': entrypoint, 'source_files': files}),
                        str(error) + '\n' + ((result or {}).get('output', '')[-6000:]), 0, 0, expected_head)
            shutil.rmtree(stage)
            raise
        finally:
            shutil.rmtree(scratch)

    def built(self, saved):
        files = saved['design']['source_files']
        atomic_json(self.meta, {'base_head': saved['head'], 'source_mode': True,
                               'baseline': {f['path']: f['sha256'] for f in files if f['path'] not in RESERVED}})

    async def inspect(self, revision, query):
        self.ensure()
        if not revision:
            raise ValueError('Build a model before inspecting geometry')
        stage = Path(tempfile.mkdtemp(prefix='.inspect-', dir=self.project.path))
        try:
            for name in ('model.FCStd', 'geometry.json'):
                shutil.copyfile(self.project.file(revision, name), stage / name)
            atomic_json(stage / 'query.json', query)
            result = await execute(stage, ['/opt/cad/usr/bin/python', '/source_kernel.py', 'inspect'],
                                   helpers=('source_kernel.py', 'cad_worker.py', 'design.py', 'stl_audit.py'))
            if result['exitCode']:
                raise ValueError(build_error(result['output']))
            report = json.loads((stage / 'inspection.json').read_text())
            report['revision'] = revision
            if (stage / 'inspection.png').is_file():
                from .attachments import store_image
                image = store_image(self.project.path / 'research-images', (stage / 'inspection.png').read_bytes(),
                                    f'{revision} inspection: {query.get("view", "custom")}')
                report['image'] = image
            return self.save_evidence(report)
        finally:
            shutil.rmtree(stage)
