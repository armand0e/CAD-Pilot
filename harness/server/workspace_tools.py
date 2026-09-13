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


TOOLS = [
    definition('create_path_body', 'Create an editable Python body module from a custom SVG path outline: M/L/H/V/C/S/Q/T/A/Z, relative forms, nested holes. Extrude it or revolve its radius/height profile. Returns a draft file; import its shape into model.py parts and cad_build. Curves remain native; SVG A arcs use cubic approximation. This does not replace the saved model.', {
        'name': STRING, 'path': STRING, 'operation': {'type':'string','enum':['extrude','revolve']},
        'height': NUMBER, 'angle': NUMBER, 'plane': {'type':'string','enum':['xy','xz','yz']}, 'scale': NUMBER,
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
    definition('spec_update', 'Replace design-spec.json with the version from spec_read. Requirements/decisions/references have id,text,origin(user/sourced/assumed),evidence IDs,status(open/implemented/verified),features. Keep all still-applicable requirements and cite actual inputs/pages/images/measurements. File edits through Pi are also supported.', {
        'expected_version': {'type': 'integer'}, 'specification': {'type': 'object', 'properties': {
            'objective': STRING, 'coordinates': STRING,
            **{k: {'type': 'array', 'items': {'type': 'object', 'properties': {
                'id': STRING, 'text': STRING, 'origin': {'type': 'string', 'enum': ['user','sourced','assumed']},
                'evidence': STRINGS, 'status': {'type': 'string', 'enum': ['open','implemented','verified']}, 'features': STRINGS},
                'required': ['id','text','origin','evidence'], 'additionalProperties': False}} for k in ('requirements','decisions','references')},
            'open_questions': STRINGS, 'addressed_inputs': STRINGS},
            'required': ['objective','coordinates','requirements','decisions','references','open_questions','addressed_inputs'], 'additionalProperties': False}}, ('expected_version','specification')),
]
NAMES = [t['function']['name'] for t in TOOLS]
PI_NAMES = ['read', 'write', 'edit', 'bash']
PROMPT = '''You are CADPilot, a CAD design agent. Pi owns your conversation, steering and compaction.
Use inspect at the start/resume. Read /work/CAD_GUIDE.md and /work/design-spec.json.
You have Pi's read/write/edit/bash tools in an isolated persistent CAD workspace.
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
When product dimensions are missing, delegate their investigation with
research_dimensions. Give the exact part/revision, missing dimensions, relevant
context and image IDs. Its separate Pi conversation returns documented dimensions,
sources and unknowns without filling your context with the whole investigation.
Carry its useful findings into the spec, keeping uncertainty and coordinate datums.
Use cad_inspect/cad_render for underside, sections, body isolation, actual faces,
clearance and intersection checks. Reopen images with view_image as necessary.
Measured evidence is scoped to its revision and geometric query; geometry validity
is not proof of requested features or fit. Keep unresolved assumptions visible.
Ask a focused question only when a new choice materially affects the result.
End with what changed, assumptions and what remains unverified. Respect steering.
'''


async def dispatch(bridge, name, args):
    runner, ctx = bridge.runner, bridge.ctx
    project = ctx['project']
    work = SourceWorkspace(project)
    work.ensure()
    events = runner.transcript.read() if runner.transcript else runner.events
    work.record_inputs(events)
    if name == 'create_path_body':
        body = args['name']
        if not isinstance(body, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', body) or not isinstance(args['path'], str) or len(args['path']) > 100000:
            raise ValueError('Provide a body identifier and SVG path data (up to 100000 characters)')
        operation = args['operation']
        if operation not in ('extrude', 'revolve'):
            raise ValueError('Path body operation is extrude or revolve')
        path = 'profiles/' + body + '.py'
        if work.file(path).exists():
            raise ValueError('That profile already exists. Edit its source with Pi edit instead of overwriting it.')
        params = {k: args[k] for k in ('scale','flip_y') if k in args}
        params.update({'height': args['height'], 'plane': args.get('plane','xy')} if operation=='extrude' else {'angle': args.get('angle',360)})
        content = f'from cad_paths import {operation}\n\noutline = {args["path"]!r}\nshape = {operation}(outline, ' + ', '.join(f'{k}={v!r}' for k,v in params.items()) + ')\n'
        await work.fs({'action':'write','path':path,'content':content})
        return {'file':path,'saved_geometry_changed':False,
                'next':f'In model.py: from profiles.{body} import shape as {body}; then include "{body}": {body} in parts and call cad_build.'}
    if name == '__workspace':
        return await work.fs(args)
    if name == 'spec_read':
        return work.spec_context(**args)
    if name == 'spec_update':
        value = work.update_spec(args['specification'], args['expected_version'])
        runner.emit({'t': 'specification', 'project_id': project.id, 'specification': value, 'timeline': False})
        return value
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
    return {'ok': True, 'head': saved['head'], 'geometry': geometry_summary(saved['geometry']),
            'files': work.describe()['files'], 'specification': work.spec_context(), 'warning': warning}


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
