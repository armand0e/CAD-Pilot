"""CAD-specific tools around Pi's own file and shell tools."""
import shutil
import re

from .edit_guard import NativeEditBlocked, native_edit_blocker
from .presentation import present_revision
from .projects import atomic_json
from .source_workspace import SourceWorkspace

STRING = {'type': 'string'}
NUMBER = {'type': 'number'}
STRINGS = {'type': 'array', 'items': STRING}


def definition(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}}}


PLANE = {'type': 'string', 'enum': ['xy', 'xz', 'yz']}
TOOLS = [
    definition('path_preview', 'Check an SVG path outline before building with it: returns bounds, area, winding, which subpaths are holes, warnings (self-intersections, unclosed outlines) and a rendered picture with a millimetre grid, start point and direction. Costs no CAD build. Use it for any hand-written outline; generators from cad_paths (rect, circle, slot, polygon, hexagon, d_shape, with_holes) produce valid paths directly.', {
        'path': STRING, 'plane': PLANE, 'scale': NUMBER, 'flip_y': {'type': 'boolean'}, 'label': STRING}, ('path',)),
    definition('create_path_body', 'Create an editable Python body module from an SVG path outline (M/L/H/V/C/S/Q/T/A/Z, relative forms, nested holes): extrude it, or revolve its radius/height profile drawn in XZ. The outline is validated and previewed first. Writes profiles/<name>.py (import its shape into model.py parts, then cad_build) and profiles/<name>.svg for OpenSCAD import(). Curves stay native; A arcs use cubic approximation. This does not replace the saved model.', {
        'name': STRING, 'path': STRING, 'operation': {'type':'string','enum':['extrude','revolve']},
        'height': NUMBER, 'angle': NUMBER, 'plane': PLANE, 'scale': NUMBER,
        'flip_y': {'type':'boolean'}}, ('name','path','operation')),
    definition('cad_build', 'Build the editable FreeCAD Python or OpenSCAD workspace, independently validate geometry, save a revision and open it. Read CAD_GUIDE.md first. Failed builds preserve draft files and the saved model.', {'entrypoint': STRING}, ('entrypoint',)),
    definition('cad_checkout', 'Refresh source files from the CURRENT saved revision after restoring or editing with typed tools. Unsaved source changes are protected unless discard_changes=true; never discard user changes without instruction.', {'discard_changes': {'type': 'boolean'}}),
    definition('cad_inspect', 'Inspect actual saved geometry: objects; paginated faces; minimum distance/intersection between a and b (Object or Object:FaceN/EdgeN); or axis-aligned cross section at a coordinate. Indices belong to the returned revision. The result gets a durable measurement evidence ID.', {
        'query': {'type': 'string', 'enum': ['objects', 'faces', 'measure', 'section']}, 'revision': STRING,
        'object': STRING, 'a': STRING, 'b': STRING, 'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'axis': {'type': 'string', 'enum': ['x','y','z']}, 'at': NUMBER}, ('query',)),
    definition('cad_render', 'Render a saved revision from any direction; isolate bodies, highlight Body or Body:FaceN, or clip at a section plane. Returns an image ID and pixels. Does not change geometry.', {
        'revision': STRING, 'view': {'type': 'string', 'enum': ['iso','top','bottom','front','back','left','right']},
        'direction': {'type': 'array', 'items': NUMBER, 'minItems': 3, 'maxItems': 3}, 'bodies': STRINGS, 'highlight': STRING,
        'section': {'type': 'object', 'properties': {'axis': {'type': 'string', 'enum': ['x','y','z']}, 'at': NUMBER,
            'keep': {'type': 'string', 'enum': ['above','below']}}, 'required': ['axis','at'], 'additionalProperties': False}}),
    definition('spec_read', 'Read the persistent editable specification, immutable user input evidence and geometry evidence. Includes recent unaddressed corrections and stale verification. Page through older evidence with offsets. This survives Pi compaction and restarts.', {
        'input_offset':{'type':'integer','minimum':0},'evidence_offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50}}),
    definition('spec_update', 'Patch only changed specification fields using expected_version from spec_read. Row arrays upsert by id; omitted rows/fields are preserved. New rows need id,text,origin,evidence. addressed_inputs adds linked input IDs; open_questions replaces that list. Retire a row with status=retired and retirement reason/user evidence; never delete it. verified needs a scoped verification check (measurement, visual or task); implemented does not. Full file edits through Pi remain supported with deletion protection.', {
        'expected_version': {'type': 'integer'}, 'specification': {'type': 'object', 'properties': {
            'objective': STRING, 'coordinates': STRING,
            **{k: {'type': 'array', 'items': {'type': 'object', 'properties': {
                'id': STRING, 'text': STRING, 'origin': {'type': 'string', 'enum': ['user','sourced','assumed']},
                'evidence': STRINGS, 'status': {'type': 'string', 'enum': ['open','implemented','verified','retired']}, 'features': STRINGS,
                'verification': {'type':'object', 'properties': {
                    'kind': {'type':'string','enum':['measurement','visual','task']},
                    'evidence': STRING, 'field': {'type':'string', 'description':'JSON pointer selecting ONE NUMBER from cad_inspect, e.g. /objects/0/bounds_mm/2 or /contours/1/bounds/bounds_mm/0. Never select an entire bounds object or array.'},
                    'expected': NUMBER, 'tolerance': NUMBER,
                    'file': STRING, 'note': STRING}, 'required':['kind'], 'additionalProperties':False},
                'retirement': {'type':'object','properties':{'reason':STRING,'evidence':STRINGS},
                    'required':['reason','evidence'],'additionalProperties':False}},
                'required': ['id'], 'additionalProperties': False}} for k in ('requirements','decisions','references')},
            'open_questions': STRINGS, 'addressed_inputs': STRINGS},
            'additionalProperties': False}}, ('expected_version','specification')),
]
NAMES = [t['function']['name'] for t in TOOLS]
PI_NAMES = ['read', 'write', 'edit', 'bash']
PROMPT = '''You are CADPilot, a CAD design agent. Pi owns your conversation, steering and compaction.
Use inspect at the start/resume. Read /work/CAD_GUIDE.md and /work/design-spec.json.
You have Pi's read/write/edit/bash tools in an isolated persistent CAD workspace
without network: fetch pages and PDFs with research, and STEP/STL/DXF/SVG files
with import_reference (they appear under /work/references/ for FreeCAD import).
Prefer editable FreeCAD Python (model.py) or OpenSCAD (model.scad) and cad_build for
general CAD work: use the full CAD APIs, functions, sketches and patterns as needed.
Batch related source edits before a build. Tool results show actual saved geometry.
The convenient typed CAD tools remain usable on older operation-based projects;
they cannot modify a source build. Their expression/bounding-box restrictions do
not apply to Python/OpenSCAD source. Read each tool's argument description.
Maintain design-spec.json: requirements, dimensions with units and origins,
accepted choices, image observations/crops, coordinate conventions, open questions
and links to implemented bodies/features. User inputs have immutable evidence IDs.
Read and address pending corrections before continuing. Do not delete unrelated
requirements when changing one feature. Never claim an assumption was user-approved
or sourced without its evidence. Research missing product measurements, open the
actual documentation, and examine supplied images; search snippets aren't verified
dimensions and pictures without scale don't establish exact dimensions.
First identify what the request refers to. If a product, part or acronym is
unfamiliar and one or two searches don't resolve it, stop and ask the user to
confirm the exact name or what it is (ask_question, offering your best guesses as
options) rather than searching name variants repeatedly or assuming the wrong
subject; an empty search means the name is likely wrong, not that you should rephrase.
Once identified, if you still lack a critical interface dimension (mounting-hole
spacing, connector position, mating size) that no source gives, ask for it or state
the assumption in the spec; never silently invent a mounting interface.
When product dimensions are missing, delegate their investigation with
research_dimensions: exact part/revision, the dimensions needed (most important
first: outline, mounting holes, connector positions per edge; 3-6 items), relevant
context and reference image IDs. It returns documented values with datums and
quotes, drawing readings to confirm, and explicit unknowns, without filling your
context. Independent parts can be delegated in the same turn. Carry its findings
into the spec with their uncertainty and coordinate datums, then build with them
right away; do not repeat its searches or re-read the drawings it already cited
(its values carry page/crop evidence). Put its unknowns to the user in one
ask_question, or state the assumption you will use, instead of chasing them yourself.
Draw custom outlines as SVG paths: cad_paths generators (rect, circle, slot, polygon,
hexagon, d_shape, with_holes) plus extrude/revolve/loft/pipe/cut_through. Run
path_preview on any hand-written outline before building; it shows the shape.
Use cad_inspect/cad_render for underside, sections, body isolation, actual faces,
clearance and intersection checks. Reopen images with view_image as necessary.
Use spec_update for small patches instead of rewriting every requirement. Retire
superseded requirements explicitly with a reason and the correcting user input ID.
Mark work implemented when done; use verified only for a concrete numeric check,
visual observation or task file check described in CAD_GUIDE.md. An overview with
context_file is a preview; Pi read can retrieve its full records without a new search.
Measured evidence is scoped to its revision and geometric query; geometry validity
is not proof of requested features or fit. Keep unresolved assumptions visible.
Ask a focused question only when a new choice materially affects the result.
A question that needs no geometry gets a direct answer with its sources; the spec
is for things you build.
End with what changed, assumptions and what remains unverified. Respect steering.
'''


async def dispatch(bridge, name, args):
    runner, ctx = bridge.runner, bridge.ctx
    project = ctx['project']
    work = SourceWorkspace(project)
    bridge.record_inputs(work)
    if name in ('path_preview', 'create_path_body'):
        from .svg_path import PathError, preview
        from .attachments import image_path, store_image
        plane, scale, flip_y = args.get('plane', 'xy'), args.get('scale', 1), bool(args.get('flip_y', False))
        if not isinstance(args.get('path'), str) or len(args['path']) > 100000 or plane not in ('xy', 'xz', 'yz'):
            raise ValueError('Provide SVG path data (up to 100000 characters) and a plane of xy, xz or yz')
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not 0 < scale < 1e6:
            raise ValueError('scale is millimetres per path unit (positive)')
        try:
            png, report = preview(args['path'], scale=scale, flip_y=flip_y, plane=plane)
        except PathError as error:
            raise ValueError('Invalid path: ' + str(error)) from None
        label = args.get('label') if isinstance(args.get('label'), str) and args.get('label') else args.get('name') or 'outline'
        image = store_image(project.path / 'research-images', png, f'Path preview: {label[:80]}')
        runner.pending_images.append({**image, 'path': str(image_path(project.path, image['id']))})
        result = {k: report[k] for k in ('subpaths', 'holes', 'bounds', 'area_mm2', 'start', 'warnings', 'valid')}
        result['outlines'] = [{k: o[k] for k in ('index', 'role', 'winding', 'closed', 'bounds', 'area_mm2')} for o in report['outlines']]
        result['image'] = image
        result['plane'] = plane
        if name == 'path_preview':
            result['next'] = ('The attached picture shows the outline in plane coordinates. Fix any warning before extruding; '
                              'then use cad_paths in model.py (extrude/revolve/loft/pipe/cut_through) or create_path_body.')
            return result
        body = args['name']
        if not isinstance(body, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', body):
            raise ValueError('Provide a body identifier (letters, digits, underscores)')
        if not report['valid']:
            raise ValueError('The outline cannot become a solid: ' + ' '.join(report['warnings']))
        operation = args['operation']
        if operation not in ('extrude', 'revolve'):
            raise ValueError('Path body operation is extrude or revolve')
        path = 'profiles/' + body + '.py'
        if work.file(path).exists():
            raise ValueError('That profile already exists. Edit its source with Pi edit instead of overwriting it.')
        params = {k: args[k] for k in ('scale', 'flip_y') if k in args}
        if operation == 'extrude':
            if not isinstance(args.get('height'), (int, float)) or isinstance(args.get('height'), bool) or not args['height']:
                raise ValueError('extrude needs a nonzero height')
            params.update({'height': args['height'], 'plane': plane})
        else:
            params.update({'angle': args.get('angle', 360)})
        content = (f'from cad_paths import {operation}\n\n# Preview {image["id"]}: {report["subpaths"]} subpath(s), {report["holes"]} hole(s), '
                   f'{report["bounds"]["size"][0]} x {report["bounds"]["size"][1]} mm\noutline = {args["path"]!r}\nshape = {operation}(outline, '
                   + ', '.join(f'{k}={v!r}' for k, v in params.items()) + ')\n')
        await work.fs({'action': 'write', 'path': path, 'content': content})
        # A self-contained SVG at the origin in physical millimetres: OpenSCAD's
        # import() then reproduces the outline exactly, y up, regardless of dpi.
        from .svg_path import parse, serialize, transform
        (x0, y0), (w, h) = report['bounds']['min'], report['bounds']['size']
        parsed, _ = parse(report['normalized'])
        flipped = serialize(transform(parsed, flip_y=True, dx=-x0, dy=y0 + h))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">'
               f'<path fill-rule="evenodd" d="{flipped}"/></svg>\n')
        await work.fs({'action': 'write', 'path': 'profiles/' + body + '.svg', 'content': svg})
        offset = f'translate([{x0}, {y0}]) ' if (x0 or y0) else ''
        result.update(file=path, svg='profiles/' + body + '.svg', svg_origin=[x0, y0], saved_geometry_changed=False,
                      next=f'In model.py: from profiles.{body} import shape as {body}; include "{body}": {body} in parts and call cad_build. '
                           f'OpenSCAD: {offset}linear_extrude(height=H) import("profiles/{body}.svg"); (millimetres, y up; the file starts at the origin).')
        return result
    if name == '__workspace':
        return await work.fs(args)
    if name == 'spec_read':
        return work.spec_context(**args)
    if name == 'spec_update':
        value = work.update_spec(args['specification'], args['expected_version'], patch=True)
        runner.emit({'t': 'specification', 'project_id': project.id, 'specification': value, 'timeline': False})
        return {'version': value['version'], 'updated_ids': [r['id'] for key in ('requirements','decisions','references')
                for r in args['specification'].get(key, [])], 'path': '/work/design-spec.json'}
    if name == 'cad_checkout':
        return work.checkout(ctx['expected_head'], args.get('discard_changes', False))
    if name in ('cad_inspect', 'cad_render'):
        query = dict(args)
        revision = query.pop('revision', None) or ctx['expected_head']
        if name == 'cad_render':
            query['query'] = 'render'
        value = await work.inspect(revision, query)
        if value.get('image'):
            from .attachments import image_path
            runner.pending_images.append({**value['image'], 'path': str(image_path(project.path, value['image']['id']))})
        return value
    if name != 'cad_build':
        raise ValueError('Unknown workspace tool')
    runner.set_phase('building')
    saved, warning = await build_revision(runner.session, args['entrypoint'], ctx['expected_head'], runner.research)
    ctx.update(saved=saved, geometry=saved['geometry'], expected_head=saved['head'], changed=True,
               state={'format': 'source-v1', 'bodies': saved['geometry']['parts']})
    runner.completed_steps += 1
    runner.emit({'t': 'artifact', 'project': saved})
    if warning:
        runner.emit({'t': 'note', 'message': warning})
    from .agent import geometry_summary
    return work.context_result('build-result', {'ok': True, 'head': saved['head'], 'geometry': geometry_summary(saved['geometry']),
            'files': work.describe()['files'], 'specification': work.spec_context(), 'warning': warning})


async def build_revision(session, entrypoint, expected_head, research=None):
    project = session.project
    work = SourceWorkspace(project)
    if project.read()['head'] != expected_head:
        raise ValueError('Project changed; reload before building')
    blocker = await native_edit_blocker(session)
    if blocker:
        raise NativeEditBlocked(blocker)
    stage = None
    try:
        stage = await work.prepare(entrypoint, expected_head)
        blocker = await native_edit_blocker(session)
        if blocker:
            raise NativeEditBlocked(blocker)
        if research and research['sources']:
            atomic_json(stage / 'research.json', research)
        saved = project.commit(stage, expected_head)
        stage = None
    finally:
        if stage is not None:
            shutil.rmtree(stage)
    work.built(saved)
    (project.path / 'draft-workspace.json').unlink(missing_ok=True)
    warning = None
    try:
        await present_revision(session)
    except Exception as error:
        warning = 'Revision saved; viewport could not open it: ' + str(error)[:300]
    return saved, warning
