"""A separate Pi session investigates dimensions and returns only cited findings."""
import copy
import json
import math
from types import SimpleNamespace

from .attachments import image_path, store_image
from .projects import Project, atomic_json
from .research import validate_notes
from .workspace_tools import definition, STRING, STRINGS

RESEARCH_TOOLS = {'research', 'research_images', 'view_image', 'recall_facts', 'design_notes', 'inspect', 'submit_research'}
DELEGATE = definition('research_dimensions', 'Delegate missing product dimensions to an isolated Pi research agent. Give an exact part identity, dimensions to investigate and relevant context/reference IDs. It reads primary documentation, examines references and returns a compact cited report with unknowns; its full investigation stays outside this conversation. It cannot edit CAD.', {
    'part_identity': STRING, 'dimensions': STRINGS, 'context': STRING, 'reference_ids': STRINGS}, ('part_identity','dimensions'))
REPORT = definition('submit_research', 'Submit the investigation findings. Every documented dimension must have an exact supporting quote from an opened page/PDF. Put unsupported estimates and conflicts in assumptions/unknowns. This records evidence, not mechanical fit certification.', {
    'part_identity': STRING, 'summary': STRING,
    'dimensions': {'type':'array','maxItems':12,'items':{'type':'object','properties':{
        'name':STRING,'value':{'type':'number'},'unit':{'type':'string','enum':['mm','deg','count']},
        'datum':STRING,'source_id':STRING,'quote':STRING},'required':['name','value','unit','datum','source_id','quote'],'additionalProperties':False}},
    'assumptions':STRINGS,'unknowns':STRINGS}, ('part_identity','summary','dimensions','assumptions','unknowns'))
PROMPT = '''You are CADPilot's dimension researcher, a separate Pi research session.
Investigate ONLY the supplied part identity, missing dimensions and relevant references.
You have no CAD editing tools. Do not model anything or invent missing specifications.
Identify exact product/revision before mixing dimensions from different variants. Prefer
manufacturer mechanical drawings, datasheets and official CAD files. Read original pages
and PDFs, not only snippets. Cross-check critical sizes and resolve conflicts explicitly.
Investigate board outlines, thickness, mounting-hole diameters/pitches and coordinate
datums, connector locations/keep-outs and applicable tolerances when requested. Record
the units and datum for every coordinate. User images establish appearance/variant;
do not infer precise dimensions from unscaled pixels. Use view_image to reopen/crop.
Keep researching while useful evidence remains. If documentation doesn't establish a
requested value, return that as unknown with a concrete follow-up; never replace it with
a confident guess. Do not ask the user directly; the parent handles clarification.
Call submit_research with a compact documented-dimension table, exact supporting quotes,
assumptions and unresolved questions. All source IDs must come from pages you opened.
Your full research transcript remains here; only the validated report goes to the modeler.
Then finish with a short status message. Pi owns your loop, conversation and compaction.
'''


def submit(runner, report):
    for key in ('part_identity','summary'):
        if not isinstance(report.get(key),str) or not 1 <= len(report[key]) <= 2000:
            raise ValueError('Report identity and summary must be short nonempty text')
    dimensions=report.get('dimensions')
    if not isinstance(dimensions,list) or len(dimensions)>12:
        raise ValueError('Return up to 12 documented dimensions; split larger investigations')
    facts=[]
    for row in dimensions:
        if not isinstance(row,dict) or set(row)!={'name','value','unit','datum','source_id','quote'}:
            raise ValueError('Each dimension needs name,value,unit,datum,source_id,quote')
        if isinstance(row['value'],bool) or not isinstance(row['value'],(float,int)) or not math.isfinite(row['value']) or row['unit'] not in ('mm','deg','count'):
            raise ValueError('Dimension values must be finite, with unit mm, deg or count')
        if any(not isinstance(row[k],str) or not row[k].strip() or len(row[k])>600 for k in ('name','datum','source_id','quote')):
            raise ValueError('Each dimension needs short text identifying its datum and source')
        facts.append({'statement':f'{row["name"]}: {row["value"]} {row["unit"]}; datum: {row["datum"]}',
                      'source_id':row['source_id'],'quote':row['quote']})
    notes=validate_notes({'facts':facts,'assumptions':report.get('assumptions'),'unknowns':report.get('unknowns')},runner.research['sources'])
    ids={f['source_id'] for f in facts}
    sources=[]
    for source in runner.research['sources']:
        if source['id'] not in ids:
            continue
        quotes='\n'.join(f['quote'] for f in facts if f['source_id']==source['id'])
        sources.append({k:v for k,v in source.items() if k in ('id','url','title','domain','kind','retrievedAt')} |
                       {'opened':True,'text':quotes,'excerpt':quotes,'investigation':runner.session.project.id})
    result={**report,'notes':notes,'sources':sources,'status':'reported',
            'scope':'Documented findings with supporting quotes; interpretation and mechanical fit still require checking.'}
    atomic_json(runner.session.project.path/'dimension-report.json',result)
    runner.dimension_report=result
    return {'ok':True,'documented_dimensions':len(dimensions),'unknowns':notes['unknowns']}


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
    parent.emit({'t':'note','message':f'Dimension researcher started for {args["part_identity"]}. Its investigation has a separate conversation.'})
    try:
        await child._native_model(prompt)
    finally:
        await child.wait_stopped()
    result=child.dimension_report or {'status':'incomplete','summary':'The researcher stopped without a documented report.',
                                    'dimensions':[],'unknowns':dimensions,'sources':[]}
    result={**result,'investigation_id':project.id,
            'transcript_url':f'/api/projects/{parent.session.project.id}/investigations/{project.id}'}
    if child.dimension_report:
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
    parent.emit({'t':'note','message':f'Dimension researcher returned {len(result["dimensions"])} documented dimension(s), with {len(result["unknowns"])} unresolved item(s).'})
    return result
