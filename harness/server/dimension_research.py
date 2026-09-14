"""A separate Pi session investigates dimensions and returns only cited findings."""
import copy
import asyncio
import json
import math
from types import SimpleNamespace

from .attachments import image_path, image_provenance, store_image
from .projects import Project, atomic_json
from .research import validate_notes
from .workspace_tools import definition, STRING, STRINGS
from .research_progress import ResearchProgress

RESEARCH_TOOLS = {'research', 'research_images', 'view_image', 'recall_facts', 'design_notes', 'inspect', 'submit_research'}
DELEGATE = definition('research_dimensions', 'Delegate missing product dimensions to an isolated Pi research agent. Give an exact part identity, dimensions to investigate and relevant context/reference IDs. It reads primary documentation, examines references and returns a compact cited report with unknowns; its full investigation stays outside this conversation. It cannot edit CAD.', {
    'part_identity': STRING, 'dimensions': STRINGS, 'context': STRING, 'reference_ids': STRINGS}, ('part_identity','dimensions'))
REPORT = definition('submit_research', 'Finish the investigation with the available findings. For text evidence, quote an opened page/PDF. For dimensions read from a drawing, also provide image_id and transcribe the visible dimension label in quote; use the document source_id. Drawing readings remain explicitly unverified visual observations. Unsupported rows become unresolved findings without rejecting the entire report. Then finish; do not resubmit the whole report to chase gaps.', {
    'part_identity': STRING, 'summary': STRING,
    'dimensions': {'type':'array','items':{'type':'object','properties':{
        'name':STRING,'value':{'type':'number'},'unit':{'type':'string','enum':['mm','deg','count']},
        'datum':STRING,'source_id':STRING,'quote':STRING,
        'image_id':{'type':'string','description':'For visual evidence only: ID of an attached drawing page/image or its view_image crop. Quote the actual printed dimension, never a measurement estimated from pixels.'}},
        'required':['name','value','unit','datum','source_id','quote'],'additionalProperties':False}},
    'assumptions':STRINGS,'unknowns':STRINGS}, ('part_identity','summary','dimensions','assumptions','unknowns'))
PROMPT = '''You are CADPilot's dimension researcher, a separate Pi research session.
Investigate ONLY the supplied part identity, missing dimensions and relevant references.
You have no CAD editing tools. Do not model anything or invent missing specifications.
Identify exact product/revision before mixing dimensions from different variants. Prefer
manufacturer mechanical drawings, datasheets and official CAD files. Read original pages
and PDFs, not only snippets. Cross-check critical sizes and resolve conflicts explicitly.
Search results include source IDs, URLs and snippets; opened pages include text and links.
Follow those returned URLs verbatim. Never guess PDF filenames or enumerate URL variants.
If a link fails, use another returned link or a focused search to find an accessible source.
Investigate board outlines, thickness, mounting-hole diameters/pitches and coordinate
datums, connector locations/keep-outs and applicable tolerances when requested. Record
the units and datum for every coordinate. User images establish appearance/variant;
do not infer precise dimensions from unscaled pixels. Use view_image to reopen/crop.
Finish once the requested dimensions are documented, or the available evidence leaves
specific gaps. Do not keep searching for an exhaustive report: return partial findings.
If documentation doesn't establish a requested value, return it as unknown with a concrete follow-up; never replace it with
a confident guess. Do not ask the user directly; the parent handles clarification.
Call submit_research with a compact dimension table, supporting quotes, assumptions and
unresolved questions. A requested category may require several individual measurements.
For text, use the opened page/PDF source_id and an exact quote with enough context to
identify the measurement. For drawing images, also give image_id (including crop IDs)
and transcribe the printed dimension label as quote. Such readings are recorded as
visual observations needing confirmation, not as text-verified facts. An image's ID
is distinct from its document source_id; research results provide both. Do not treat
unscaled photos or inferred connector positions as documented measurements.
Submission records valid findings and returns unsupported rows as unresolved questions.
After submission, finish instead of repeatedly rewriting the report or researching gaps.
Your full research transcript remains here; only the compact report goes to the modeler.
Then finish with a short status message. Pi owns your loop, conversation and compaction.
'''


def submit(runner, report):
    for key in ('part_identity','summary'):
        if not isinstance(report.get(key),str) or not 1 <= len(report[key]) <= 2000:
            raise ValueError('Report identity and summary must be short nonempty text')
    dimensions=report.get('dimensions')
    if not isinstance(dimensions,list):
        raise ValueError('dimensions must be a list')
    for field in ('assumptions', 'unknowns'):
        if not isinstance(report.get(field), list) or any(not isinstance(s,str) or not s.strip() for s in report[field]):
            raise ValueError(f'{field} must be a list of nonempty strings')
    facts, accepted, rejected, visual_notes = [], [], [], []
    known = {s['id']: s for s in runner.research['sources']}
    for index, row in enumerate(dimensions):
        try:
            accepted_row, fact = _dimension(runner, row, known)
        except (ValueError, KeyError) as error:
            rejected.append({'row':index + 1, 'dimension':row, 'issue':str(error)})
            continue
        accepted.append(accepted_row)
        if fact:
            facts.append(fact)
        else:
            visual_notes.append(f"Unverified drawing reading: {row['name']}: {row['value']} {row['unit']}; datum: {row['datum']}; "
                                f"source {accepted_row['source_id']}, image {accepted_row['evidence']['image_id']}, label {row['quote']!r}. Confirm interpretation before use.")
    unknowns = report['unknowns'] + [f"Dimension {r['row']} ({r['dimension'].get('name', 'unnamed') if isinstance(r['dimension'],dict) else 'invalid row'}): {r['issue']}" for r in rejected]
    # The report preserves full explanations. Saved notes are compact context;
    # their legacy per-call list limit does not limit an investigation's findings.
    notes = {'facts':facts, 'assumptions':[s.strip()[:600] for s in report['assumptions'] + visual_notes],
             'unknowns':[s.strip()[:600] for s in unknowns]}
    validate_notes(notes, runner.research['sources'], max_entries=None)
    ids={row['source_id'] for row in accepted}
    sources=[]
    for source in runner.research['sources']:
        if source['id'] not in ids:
            continue
        quotes='\n'.join(f['quote'] for f in facts if f['source_id']==source['id'])
        sources.append({k:v for k,v in source.items() if k in ('id','url','title','domain','kind','retrievedAt','retrieved_at','images')} |
                       {'opened':True,'text':quotes,'excerpt':quotes,'investigation':runner.session.project.id})
    result={**report,'dimensions':accepted,'unknowns':unknowns,'unverified_dimensions':rejected,
            'notes':notes,'sources':sources,'status':'incomplete' if rejected else 'reported',
            'documented_dimensions':len(facts),'visual_dimensions':len(visual_notes),
            'scope':'Text citations are checked against retrieved text. Drawing readings are unverified visual observations. Neither establishes correct interpretation or mechanical fit.'}
    if rejected:
        result['summary'] += f"\n{len(rejected)} submitted measurement(s) could not be supported; see unresolved findings before using these dimensions."
    atomic_json(runner.session.project.path/'dimension-report.json',result)
    runner.dimension_report=result
    runner.emit({'t':'research_report','documented_dimensions':len(facts),'visual_dimensions':len(visual_notes),
                 'unresolved_dimensions':len(rejected)})
    return {'ok':True,'status':result['status'],'documented_dimensions':len(facts),'visual_dimensions':len(visual_notes),
            'unresolved_dimensions':[{'row':r['row'],'issue':r['issue']} for r in rejected],
            'next':'Report saved with the available evidence and gaps. Finish now; the modeler will handle unresolved questions.'}


def _dimension(runner, row, known):
    required = {'name','value','unit','datum','source_id','quote'}
    if not isinstance(row,dict) or not required <= set(row) or set(row) - required - {'image_id'}:
        raise ValueError('Each dimension needs name,value,unit,datum,source_id,quote; image_id is optional for drawings')
    if isinstance(row['value'],bool) or not isinstance(row['value'],(float,int)) or not math.isfinite(row['value']) or row['unit'] not in ('mm','deg','count'):
        raise ValueError('Value must be finite, with unit mm, deg or count')
    if any(not isinstance(row[k],str) or not row[k].strip() or len(row[k])>600 for k in required - {'value','unit'}):
        raise ValueError('Name, datum, source_id and quote must be nonempty text up to 600 characters')
    image_id = row.get('image_id')
    # Older prompts used a drawing image ID as source_id. Resolve only genuine
    # saved image provenance, never match a number against unrelated page text.
    if image_id is None and row['source_id'] not in known:
        image_id = row['source_id']
    if image_id is not None:
        try:
            origin = image_provenance(runner.session.project.path, image_id)
        except (ValueError, OSError):
            raise ValueError('No saved drawing image matches this citation; use the source_id and image_id returned by research/view_image') from None
        candidates = [s for s in known.values() if s.get('kind') in ('pdf','page','image') and
                      (row['source_id'] == image_id or s['id'] == row['source_id'])]
        for source in candidates:
            page = next((p for p in source.get('images',[]) if p['id'] == origin['original_image_id']), None)
            if page:
                return {**row,'source_id':source['id'], 'evidence':{'kind':'drawing_image','image_id':image_id,
                        **origin,'page':page.get('page'),'verification':'visual_reading_requires_confirmation'}}, None
        raise ValueError('Image is not linked to the cited opened document; user photos and unrelated images cannot establish documented dimensions')
    source = known.get(row['source_id'])
    if not source or source.get('kind') not in ('page','pdf'):
        raise ValueError('Source was not opened as a page/PDF; a search snippet cannot support a dimension')
    quote = ' '.join(row['quote'].split())
    if len(quote) < 8 or quote not in ' '.join(source.get('text','').split()):
        raise ValueError('Quote is absent from retrieved text or too short to identify a measurement. For a printed drawing label, supply image_id; otherwise leave the value unknown')
    fact = {'statement':f'{row["name"]}: {row["value"]} {row["unit"]}; datum: {row["datum"]}',
            'source_id':row['source_id'],'quote':row['quote']}
    validate_notes({'facts':[fact],'assumptions':[],'unknowns':[]}, [source])
    return {**row,'evidence':{'kind':'text_quote','verification':'quote_matched_not_interpretation'}}, fact


async def investigate(parent, args):
    if not parent.web_enabled:
        raise ValueError('Web research is disabled by the user')
    if not isinstance(args.get('part_identity'),str) or not 1 <= len(args['part_identity']) <= 1000:
        raise ValueError('Supply the exact part identity/revision to investigate')
    dimensions=args.get('dimensions')
    if not isinstance(dimensions,list) or not 1 <= len(dimensions) <= 12 or any(not isinstance(d,str) or not 1<=len(d)<=500 for d in dimensions):
        raise ValueError('Name 1–12 dimensions to investigate')
    context=args.get('context','')
    if not isinstance(context,str) or len(context)>8000:
        raise ValueError('Provide relevant context up to 8000 characters')
    refs=args.get('reference_ids',[])
    if not isinstance(refs,list) or len(refs)>16 or any(not isinstance(i,str) for i in refs):
        raise ValueError('Provide up to 16 relevant reference image IDs')
    project=Project.create(parent.session.project.path/'research-tasks','research')
    from .agent import AgentRunner
    session=SimpleNamespace(project=project,engine='hybrid',manual_changes=False,
                            app={'name':'Dimension research'},screen=SimpleNamespace(release_inputs=None),state_dir=None)
    child=AgentRunner(session,copy.deepcopy(parent.config))
    child.research_profile=True
    child.dimension_report=None
    child.web_enabled=parent.web_enabled
    reference_map=[]
    for identity in refs:
        source=image_path(parent.session.project.path,identity)
        image=store_image(project.path/'attachments',source.read_bytes(),identity)
        reference_map.append({'original_id':identity,'research_image_id':image['id']})
    child._accept_attachments([i['research_image_id'] for i in reference_map])
    prompt=json.dumps({'part_identity':args['part_identity'],'missing_dimensions':dimensions,
                       'relevant_context':context,'reference_images':reference_map})
    child.task_text=prompt
    child.emit({'t':'user','text':prompt,'new_task':True})
    child._native_inputs=[{'role':'user','content':prompt,'attachments':[i['research_image_id'] for i in reference_map]}]
    progress = ResearchProgress(parent, project, args)
    child.activity_observer = progress.consume
    try:
        await child._native_model(prompt)
        child.activity_observer = None
        await child.wait_stopped()
        result=child.dimension_report or {'status':'incomplete','summary':'The researcher stopped without a documented report.',
                                        'dimensions':[],'unknowns':dimensions,'sources':[]}
        result={**result,'investigation_id':project.id,
                'transcript_url':f'/api/projects/{parent.session.project.id}/investigations/{project.id}'}
        if child.dimension_report:
            # The modeler can reopen cited drawings without entering the child's
            # private transcript. Preserve originals and crop provenance too.
            cited_images = {identity for row in result['dimensions'] if row['evidence']['kind'] == 'drawing_image'
                            for identity in (row['evidence']['image_id'], row['evidence']['original_image_id'])}
            for identity in cited_images:
                path = image_path(project.path, identity)
                original = path.with_name(path.name + '.original.png')
                stored = store_image(parent.session.project.path/'research-images', (original if original.is_file() else path).read_bytes(), identity)
                if stored['id'] != identity:
                    raise ValueError('Drawing image identity changed while transferring research evidence')
                atomic_json(parent.session.project.path/'research-images'/(identity + '.provenance.json'),
                            image_provenance(project.path, identity))
            sources={s['id']:s for s in parent.research['sources']}
            sources.update({s['id']:s for s in result['sources']})
            parent.research['sources']=list(sources.values())
            for fact in result['notes']['facts']:
                if fact not in parent.research['notes']['facts']:
                    parent.research['notes']['facts'].append(fact)
            for field in ('assumptions','unknowns'):
                parent.research['notes'][field]=list(dict.fromkeys(parent.research['notes'][field]+result['notes'][field]))
            parent._save_conversation()
        atomic_json(project.path/'dimension-report.json',result)
        progress.finish(('incomplete' if result['status'] == 'incomplete' else 'completed') if child.dimension_report else 'failed' if progress.error else 'incomplete', result)
        return result
    except asyncio.CancelledError:
        progress.finish('cancelled')
        raise
    except Exception:
        progress.finish('failed')
        raise
    finally:
        child.activity_observer = None
        await child.wait_stopped()
