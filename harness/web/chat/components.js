import {safeURL} from './state.js';
import {ThinkingContent} from './thinking.js';
import {ResearchCard} from '../research-agents.js';

export function element(tag, className='', text='') {
  const el=document.createElement(tag); if(className) el.className=className; if(text) el.textContent=text; return el;
}
let unique=0;
const reducedMotion=()=>matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion==='reduced';
function reveal(region,open,duration=200) {
  region._reveal?.cancel();
  if(reducedMotion() || !region.isConnected){region.hidden=!open;return;}
  region.hidden=false;
  const height=region.getBoundingClientRect().height;
  region._reveal=region.animate(open?[{height:'0px',opacity:0,overflow:'hidden'},{height:`${height}px`,opacity:1,overflow:'hidden'}]:
    [{height:`${height}px`,opacity:1,overflow:'hidden'},{height:'0px',opacity:0,overflow:'hidden'}],{duration,easing:'ease-out'});
  region._reveal.onfinish=()=>{region.hidden=!open;region._reveal=null;};
}
export function disclosure(label, region, className='disclosure') {
  region.id ||= `chat-detail-${++unique}`;
  const button=element('button',className,label); button.type='button';
  button.setAttribute('aria-controls',region.id); button.setAttribute('aria-expanded','false'); region.hidden=true;
  button.onclick=()=>{const open=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(open));reveal(region,open);};
  return button;
}
export function icon(kind) {
  const paths={search:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z',
    read:'M6 3h9l4 4v14H6ZM14 3v5h5M9 12h7M9 16h7',
    cad:'m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7M12 12v10',
    thinking:'M12 3v4m0 10v4M3 12h4m10 0h4M5.6 5.6l2.8 2.8m7.2 7.2 2.8 2.8m0-12.8-2.8 2.8m-7.2 7.2-2.8 2.8',
    terminal:'m5 6 5 6-5 6m8 0h6',file:'M6 3h9l4 4v14H6ZM14 3v5h5',code:'m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18'};
  const span=element('span','activity-icon'); span.setAttribute('aria-hidden','true');
  if(!paths[kind]) {span.classList.add('fallback-dot');return span;}
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  for(const [key,val] of Object.entries({viewBox:'0 0 24 24',width:'16',height:'16',fill:'none',stroke:'currentColor','stroke-width':'1.4','stroke-linecap':'round','stroke-linejoin':'round'})) svg.setAttribute(key,val);
  const path=document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',paths[kind]); svg.append(path);span.append(svg);return span;
}
export function sourceIcon(source) {
  // No third-party favicon beacon. Only trusted same-origin raster assets may load.
  // Sources without one retain the same 12px, recognizable globe fallback.
  const wrap=element('span','source-icon'); wrap.append(icon('search'));
  if(source.favicon && /^\/static\/[\w/.-]+\.(png|ico|webp)$/.test(source.favicon)) {
    const img=element('img');img.width=12;img.height=12;img.alt='';img.src=source.favicon;
    img.onload=()=>wrap.replaceChildren(img); img.onerror=()=>img.remove();
  }
  return wrap;
}
export function externalLink(source,label) {
  const url=safeURL(source.url); if(!url) return element('span','',label);
  const a=element('a','source-link',label);a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';
  a.setAttribute('aria-label',`${label} (opens in a new tab)`);return a;
}
const dateLabel=value=>{const date=new Date(typeof value === 'number' ? value*1000:value);return Number.isFinite(date.getTime()) ? date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}) : null;};

export class SourceDetails {
  constructor(){this.el=element('section','source-detail');}
  update(source) {
    const signature=JSON.stringify(source);if(signature===this.signature)return;this.signature=signature;
    const header=element('div','source-detail-heading');header.append(sourceIcon(source),externalLink(source,source.title || source.url));
    const domain=element('div','source-meta',source.domain || safeURL(source.url)?.hostname || '');
    const state=element('span','source-read-state',source.opened ? 'Opened' : 'Discovered · not opened');domain.append(state);
    const nodes=[header,domain];
    if(source.snippet)nodes.push(element('p','source-snippet',source.snippet));
    const metadata=element('dl','source-metadata');
    const add=(label,value)=>{if(value)metadata.append(element('dt','',label),element('dd','',value));};
    add('Published',source.published_at);add('Retrieved',dateLabel(source.retrievedAt || source.retrieved_at));
    add('Source type',source.source_type ? `${source.source_type} · hostname classification` : source.kind === 'pdf' ? 'PDF document' : null);
    nodes.push(metadata);
    if(source.temporal_warning)nodes.push(element('p','source-caution',source.temporal_warning));
    if(source.truncation?.text)nodes.push(element('p','source-caution','Extracted text is truncated; open the original for the full page.'));
    this.el.replaceChildren(...nodes);
  }
}
export class SourceRow {
  constructor(source,getSource){
    this.id=source.id;this.getSource=getSource;this.el=element('div','source-row-wrap');
    const row=element('div','source-row');this.title=externalLink(source,source.title || source.url);this.title.classList.add('source-title');
    this.domain=element('span','source-domain',source.domain || safeURL(source.url)?.hostname || '');
    this.detail=new SourceDetails();this.button=disclosure('Source details',this.detail.el,'source-info');this.button.title='Source details';
    this.button.textContent='ⓘ';this.button.setAttribute('aria-label',`Details for ${source.title || source.url}`);
    const toggle=this.button.onclick;this.button.onclick=()=>{this.update();toggle();};
    row.append(sourceIcon(source),this.title,this.domain,this.button);this.el.append(row,this.detail.el);this.update();
  }
  update(){const source=this.getSource(this.id);if(source){this.detail.update(source);this.el.dataset.opened=String(source.opened);}}
}
export class SearchResults {
  constructor(getSource){this.getSource=getSource;this.rows=new Map();this.el=element('div','search-results');this.el.setAttribute('aria-label','Search sources');}
  update(sources) {
    const revealResults=!this.rows.size && sources?.length;
    for(const source of sources || []) {
      let row=this.rows.get(source.id);
      if(!row){row=new SourceRow(source,this.getSource);this.rows.set(source.id,row);this.el.append(row.el);}
      row.update();
    }
    if(revealResults && this.el.isConnected && !reducedMotion() && this.el.closest('#chat')?.dataset.follow!=='false') {
      this.el.animate([{maxHeight:'0px',opacity:0},{maxHeight:'152px',opacity:1}],{duration:300,easing:'ease-out'});
    }
  }
}
export class TimelineEntry {
  constructor(op,getSource,replaying=false) {
    this.getSource=getSource;this.el=element('div','timeline-entry');this.el.dataset.operationId=op.id;
    this.rail=element('div','timeline-rail');this.rail.append(op.kind==='read' ? sourceIcon({}) : icon(op.kind));
    this.content=element('div','timeline-content');this.details=element('div','activity-details');
    this.head=disclosure('',this.details,'activity-heading');
    this.head.addEventListener('click',()=>{this.userDisclosure=true;this.thinkingView?.pin();});
    this.label=element('span','activity-label');this.status=element('span','activity-status');
    this.dots=element('span','activity-dots');this.dots.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)this.dots.append(element('i'));
    this.chevron=element('span','activity-chevron','›');this.chevron.setAttribute('aria-hidden','true');
    this.head.append(this.label,this.dots,this.status,this.chevron);
    this.summary=element('div','activity-summary');this.sources=new SearchResults(getSource);
    this.diagnostic=element('pre','activity-raw');this.detailBody=element('div','activity-detail-body');
    this.gallery=element('div','activity-images');this.gallery.hidden=true;
    this.details.append(this.detailBody,this.diagnostic);
    this.headingLine=element('div','activity-heading-line');this.headingLine.append(this.head);
    this.content.append(this.headingLine,this.summary,this.gallery,this.sources.el,this.details);this.el.append(this.rail,this.content);
    this.update(op,replaying);
  }
  update(op,replaying=false) {
    if(this.previousKind!==op.kind){this.rail.replaceChildren(op.kind==='read'?sourceIcon({}):icon(op.kind==='proposal'?'code':op.kind));this.previousKind=op.kind;}
    this.el.dataset.status=op.status;this.el.dataset.kind=op.kind;
    const running=op.status==='running';this.dots.hidden=!(running || op.status==='preparing');
    let label=op.label || 'Tool activity',secondary=running ? 'Running' : ({preparing:op.inputComplete?'Validating input':'Drafting',completed:'Done',failed:'Failed',cancelled:'Cancelled',interrupted:'Interrupted'}[op.status] || op.status);
    if(op.kind==='thinking') {
      label=running ? op.label || 'Thinking…' : op.text ? 'Thought process' : op.label === 'Thinking…' ? 'Request processed' : op.label || 'Status';
      const seconds=op.finishedAt && op.startedAt ? Math.max(0,Math.round(op.finishedAt-op.startedAt)) : null;
      if(op.status==='completed' && seconds !== null && seconds>0)secondary=`${seconds}s`;
    } else if(op.kind==='search') {
      label=running ? 'Searching the web' : op.input.query || op.result?.query || 'Web search';
      if(op.status==='completed')secondary=`${op.result?.sources?.length || 0} results`;
    } else if(op.kind==='read') {
      const source=op.result?.sources?.[0],domain=safeURL(op.input.query || source?.url)?.hostname || 'webpage';
      label=op.status==='completed' && source ? source.title : `Fetching from ${domain}`;
      if(op.status==='completed')secondary=domain;
      if(!this.visitLink && safeURL(source?.url || op.input.query)) {
        this.visitLink=externalLink({url:source?.url || op.input.query,title:source?.title || domain},'↗');
        this.visitLink.classList.add('visit-external');this.visitLink.setAttribute('aria-label',`Open ${domain} in a new tab`);
        this.headingLine.append(this.visitLink);
      }
      if(this.visitLink && source?.url && safeURL(source.url))this.visitLink.href=source.url;
    }
    if(op.kind==='cad' && op.status==='completed')secondary=op.result?.verification_scope ? 'Saved · checked geometry' : 'Executed';
    const reviewLabel=op.review ? ({achieved:'objective observed',progress:'partial progress',blocked:'needs correction',uncertain:'unverified'}[op.review.status] || 'unverified') : null;
    if(op.kind==='cad' && reviewLabel)secondary=`Executed · ${reviewLabel}`;
    if(op.kind==='cad' && running && op.errors?.length)secondary='Correcting model';
    if(op.kind==='proposal') {
      const action=(op.input.tool || op.label || 'Tool operation').replace(/^Preparing /,'').replaceAll('_',' ');
      label=op.status==='preparing' ? `Preparing ${action}` : action.charAt(0).toUpperCase()+action.slice(1);
      if(op.input.tool==='define_parameter') {
        if(op.status==='completed')label='Defined parameter';
        const args=op.input.arguments;
        if(args?.name)label+=` · ${args.name}${args.value!==undefined ? ` = ${args.value}` : ''}`;
      }
    }
    if(this.label.textContent!==label)this.label.textContent=label;
    this.label.title=label;if(this.status.textContent!==secondary)this.status.textContent=secondary;
    this.head.setAttribute('aria-label',`${label} · ${secondary}. Details`);
    const latestError=op.errors?.at(-1)?.message;
    const correction=latestError ? 'Correction: '+latestError.split('ValueError:').at(-1).split('Geometry diagnostic:')[0].trim().slice(0,240) : '';
    this.summary.textContent=op.error || (op.kind==='proposal' ? op.status==='preparing' ? 'Input is arriving · nothing executed yet' : op.result?.message || '' : '') || (reviewLabel ? `Visual check: ${op.review.observation} This is not native geometry verification.` : correction || (running && op.kind==='search' ? op.input.query : ''));
    this.summary.hidden=!this.summary.textContent;
    this.sources.el.hidden=op.kind!=='search' || !op.result?.sources?.length;
    if(op.kind==='search')this.sources.update(op.result?.sources);
    // Pictures the model received (renders, previews, crops) are shown inline; each opens full size.
    const imageKey=JSON.stringify((op.images||[]).map(i=>i.id));
    if(imageKey!==this.imageKey){
      this.imageKey=imageKey;this.gallery.replaceChildren();
      for(const image of op.images||[]){
        const link=element('a','activity-image');link.href=image.url;link.target='_blank';link.rel='noopener noreferrer';link.title=image.label||image.id;
        const img=element('img');img.src=image.url;img.alt=image.label||'Tool image';img.loading='lazy';img.decoding='async';
        link.append(img);this.gallery.append(link);
      }
      this.gallery.hidden=!(op.images||[]).length;
    }
    if(op.kind==='thinking') {
      if(!this.thinking){
        this.thinkingView=new ThinkingContent();this.thinking=this.thinkingView.region;
        this.more=element('button','thinking-more','Show more');this.more.type='button';
        this.more.setAttribute('aria-expanded','false');this.thinking.id=`thinking-${++unique}`;this.more.setAttribute('aria-controls',this.thinking.id);
        this.more.onclick=()=>{const expanded=this.thinking.classList.toggle('expanded');this.more.textContent=expanded?'Show less':'Show more';this.more.setAttribute('aria-expanded',String(expanded));this.thinkingView.pin();};
        this.thinkingControls=element('div','thinking-controls');this.thinkingControls.append(this.more,this.thinkingView.latest);
        this.detailBody.append(this.thinking,this.thinkingControls);
      }
      const thinkingText=op.text || '';
      const hasDetails=!!thinkingText || !!op.error;
      this.head.disabled=!hasDetails;this.chevron.hidden=!hasDetails;
      this.head.setAttribute('aria-label',`${label} · ${secondary}${hasDetails?'. Details':''}`);
      if(!hasDetails){this.details.hidden=true;this.head.setAttribute('aria-expanded','false');}
      if(thinkingText && op.requestId && running && !this.autoDisclosed && !this.userDisclosure){
        this.autoDisclosed=true;this.details.hidden=false;this.head.setAttribute('aria-expanded','true');
      }
      this.thinkingView.update(thinkingText,running && !replaying);
      this.more.hidden=(op.text || '').length<650;
      if(op.truncated && !this.truncation){this.truncation=element('p','source-meta','Display limited to 32,768 characters.');this.detailBody.append(this.truncation);}
      this.diagnostic.hidden=true;
    } else {
      this.head.disabled=false;this.chevron.hidden=false;
      const signature=JSON.stringify(op.result?.sources?.[0]);
      if(op.kind==='read' && signature!==this.readSignature && op.result?.sources?.[0]) {
        this.readSignature=signature;const source=op.result.sources[0];const detail=new SourceDetails();detail.update(this.getSource(source.id)||source);
        const preview=element('p','page-excerpt',source.excerpt || source.text || 'No extracted text available.');
        this.detailBody.replaceChildren(detail.el,element('h4','','Extracted text'),preview);
      }
      const raw=op.status==='preparing' ? op.rawInput || '' : JSON.stringify({input:op.input,...(op.rawInput?{model_input:op.rawInput}:{}),...(op.result?{result:op.result}:{}),...(op.actions?.length?{actions:op.actions}:{}),...(op.errors?{errors:op.errors}:{}),...(op.review?{visual_assessment:op.review}:{}),...(op.error?{error:op.error}:{})},null,2);
      if(raw!==this.diagnostic.textContent){if(op.status==='preparing' && raw.startsWith(this.diagnostic.textContent))this.diagnostic.append(document.createTextNode(raw.slice(this.diagnostic.textContent.length)));else this.diagnostic.textContent=raw;}
      this.diagnostic.hidden=false;
    }
  }
}
export class ActivityTimeline {
  constructor(getSource) {
    this.getSource=getSource;this.entries=new Map();this.el=element('section','activity-timeline');this.el.setAttribute('aria-label','Assistant activity');
    this.list=element('div','timeline-list');this.list.id=`timeline-${++unique}`;
    this.toggle=element('button','timeline-toggle');this.toggle.type='button';this.toggle.setAttribute('aria-expanded','false');this.toggle.setAttribute('aria-controls',this.list.id);
    this.expanded=false;this.toggle.onclick=()=>{this.expanded=!this.expanded;this.layout(true);};this.el.append(this.toggle,this.list);
  }
  update(operations,replaying=false) {
    this.operations=operations;
    const ids=new Set(operations.map(op=>op.id));
    for(const [id,entry] of this.entries)if(!ids.has(id)){entry.el.remove();this.entries.delete(id);}
    for(const op of operations){let entry=this.entries.get(op.id);if(!entry){entry=new TimelineEntry(op,this.getSource,replaying);this.entries.set(op.id,entry);this.list.append(entry.el);}else entry.update(op,replaying);}
    this.layout();
  }
  layout(animate=false) {
    const ops=this.operations || [], hiddenCount=Math.max(0,ops.length-2);
    this.toggle.hidden=!hiddenCount;this.toggle.textContent=this.expanded?'Hide steps':`${ops.length} steps · show ${hiddenCount} earlier`;
    this.toggle.setAttribute('aria-expanded',String(this.expanded));
    let first=true;
    ops.forEach((op,index)=>{
      const entry=this.entries.get(op.id);
      // A deliberate disclosure is never closed or hidden by a new event.
      const hide=!this.expanded && index<hiddenCount && entry.head.getAttribute('aria-expanded')!=='true' && !entry.el.querySelector('[aria-expanded="true"]');
      if(entry.hidden!==hide){if(animate)reveal(entry.el,!hide);else entry.el.hidden=hide;entry.hidden=hide;}
      entry.el.classList.toggle('rail-first',!hide && first);if(!hide)first=false;
      entry.el.classList.toggle('rail-last',index===ops.length-1);
    });
  }
}

/* ---- Markdown rendering ------------------------------------------------------
 * A compact, dependency-free Markdown renderer that builds DOM directly (never
 * innerHTML, so model text can't inject markup). Supports headings, GFM tables,
 * ordered/unordered/nested lists, blockquotes, fenced code, thematic breaks and
 * inline bold/italic/code/links plus real [web_…] source citations. The answer
 * is re-rendered from the full text on each streamed update, but reconciled
 * block-by-block so settled paragraphs never reflow and citations keep state. */
function splitRow(line) {
  return line.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(c=>c.trim().replace(/\\\|/g,'|'));
}
const TABLE_SEP=/^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/;
function isBlockStart(line,next) {
  return /^(#{1,6})\s/.test(line) || /^\s*([-*+]|\d+[.)])\s/.test(line) || /^\s*>/.test(line)
    || /^\s*(`{3,}|~{3,})/.test(line) || /^\s*([-*_])\s*(\1\s*){2,}$/.test(line)
    || (line.includes('|') && next!=null && TABLE_SEP.test(next));
}
function parseBlocks(src) {
  const lines=(src||'').replace(/\r\n?/g,'\n').split('\n');
  const blocks=[]; let i=0;
  while(i<lines.length) {
    const line=lines[i];
    if(!line.trim()){i++;continue;}
    const fence=line.match(/^(\s*)(`{3,}|~{3,})(.*)$/);
    if(fence){
      const marker=fence[2][0],len=fence[2].length,lang=fence[3].trim(),body=[];i++;
      while(i<lines.length && !(/^\s*([`~]{3,})\s*$/.test(lines[i]) && lines[i].trim()[0]===marker && lines[i].trim().length>=len)){body.push(lines[i]);i++;}
      const closed=i<lines.length;if(closed)i++;
      blocks.push({type:'code',lang,text:body.join('\n'),key:`code:${lang}:${closed}:${body.join('\n')}`});continue;
    }
    const h=line.match(/^(#{1,6})\s+(.*)$/);
    if(h){blocks.push({type:'heading',level:h[1].length,text:h[2].replace(/\s+#+\s*$/,''),key:`h:${line}`});i++;continue;}
    if(/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)){blocks.push({type:'hr',key:`hr:${i}`});i++;continue;}
    if(/^\s*>/.test(line)){
      const body=[];while(i<lines.length && /^\s*>/.test(lines[i])){body.push(lines[i].replace(/^\s*>\s?/,''));i++;}
      blocks.push({type:'blockquote',text:body.join('\n'),key:`bq:${body.join('\n')}`});continue;
    }
    if(line.includes('|') && i+1<lines.length && TABLE_SEP.test(lines[i+1])){
      const header=splitRow(line),aligns=splitRow(lines[i+1]).map(c=>{const l=c.startsWith(':'),r=c.endsWith(':');return l&&r?'center':r?'right':l?'left':'';});
      i+=2;const rows=[];
      while(i<lines.length && lines[i].includes('|') && lines[i].trim() && !TABLE_SEP.test(lines[i])){rows.push(splitRow(lines[i]));i++;}
      blocks.push({type:'table',header,aligns,rows,key:`table:${line}:${JSON.stringify(rows)}`});continue;
    }
    if(/^\s*([-*+]|\d+[.)])\s+/.test(line)){
      const start=i;i++;
      while(i<lines.length && lines[i].trim() && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || /^\s{2,}\S/.test(lines[i])))i++;
      const raw=lines.slice(start,i);blocks.push({type:'list',lines:raw,key:`list:${raw.join('\n')}`});continue;
    }
    const para=[];while(i<lines.length && lines[i].trim() && !isBlockStart(lines[i],lines[i+1])){para.push(lines[i]);i++;}
    blocks.push({type:'paragraph',text:para.join('\n'),key:`p:${para.join('\n')}`});
  }
  return blocks;
}
function mdInline(text,parent,ctx) {
  let i=0,buf='';
  const flush=()=>{if(buf){parent.append(document.createTextNode(buf));buf='';}};
  while(i<text.length) {
    const rest=text.slice(i);let m;
    if(text[i]==='\\' && i+1<text.length && '\\`*_[]()#>~|'.includes(text[i+1])){buf+=text[i+1];i+=2;continue;}
    if((m=rest.match(/^`([^`]+)`/))){flush();parent.append(element('code','',m[1]));i+=m[0].length;continue;}
    if((m=rest.match(/^\[(web_[a-f0-9]{12})\]/)) && ctx.getSource(m[1])){flush();parent.append(ctx.citation(m[1]));i+=m[0].length;continue;}
    if((m=rest.match(/^\[([^\]]+)\]\(([^)\s]+)\)/))){const u=safeURL(m[2]);if(u){flush();const a=element('a','answer-link',m[1]);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a);i+=m[0].length;continue;}}
    if((m=rest.match(/^\*\*([^]+?)\*\*/))||(m=rest.match(/^__([^]+?)__/))){flush();const s=element('strong');mdInline(m[1],s,ctx);parent.append(s);i+=m[0].length;continue;}
    if((m=rest.match(/^\*([^*\n]+?)\*/))||(m=rest.match(/^_([^_\n]+?)_/))){flush();const em=element('em');mdInline(m[1],em,ctx);parent.append(em);i+=m[0].length;continue;}
    if(text[i]==='\n'){flush();parent.append(document.createElement('br'));i++;continue;}
    buf+=text[i];i++;
  }
  flush();
  return parent;
}
function renderList(lines,ctx) {
  const rootOrdered=/^\s*\d+[.)]/.test(lines[0]);
  const root=element(rootOrdered?'ol':'ul','answer-list');
  const stack=[{indent:lines[0].match(/^(\s*)/)[1].length,list:root,li:null}];
  for(const raw of lines) {
    const m=raw.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if(!m){const top=stack[stack.length-1];if(top.li){top.li.append(document.createTextNode(' '));mdInline(raw.trim(),top.li,ctx);}continue;}
    const indent=m[1].length,ordered=/\d/.test(m[2]),content=m[3];
    while(stack.length>1 && indent<stack[stack.length-1].indent)stack.pop();
    let top=stack[stack.length-1];
    if(indent>top.indent && top.li){const list=element(ordered?'ol':'ul','answer-list');top.li.append(list);top={indent,list,li:null};stack.push(top);}
    const li=element('li');mdInline(content,li,ctx);top.list.append(li);top.li=li;
  }
  return root;
}
function renderBlock(block,ctx) {
  switch(block.type) {
    case 'heading':return mdInline(block.text,element('h'+Math.min(block.level+1,6),'answer-h'),ctx);
    case 'hr':return element('hr');
    case 'code':{const pre=element('pre');if(block.lang)pre.dataset.lang=block.lang;pre.append(element('code','',block.text));return pre;}
    case 'blockquote':return mdInline(block.text,element('blockquote'),ctx);
    case 'list':return renderList(block.lines,ctx);
    case 'table':{
      const wrap=element('div','answer-table-wrap'),table=element('table'),thead=element('thead'),htr=element('tr');
      block.header.forEach((cell,c)=>{const th=element('th');if(block.aligns[c])th.style.textAlign=block.aligns[c];mdInline(cell,th,ctx);htr.append(th);});
      thead.append(htr);const tbody=element('tbody');
      for(const row of block.rows){const tr=element('tr');for(let c=0;c<block.header.length;c++){const td=element('td');if(block.aligns[c])td.style.textAlign=block.aligns[c];mdInline(row[c]??'',td,ctx);tr.append(td);}tbody.append(tr);}
      table.append(thead,tbody);wrap.append(table);return wrap;
    }
    default:return mdInline(block.text,element('p'),ctx);
  }
}

/** Streaming Markdown answer: re-rendered from the full text on each update but
 * reconciled block-by-block so settled content never reflows. Inline [web_…]
 * tokens become interactive citation buttons backed by the shared preview. */
export class StreamingAnswer {
  constructor(getSource) {
    this.getSource=getSource;this.el=element('div','streaming-answer msg assistant');this.body=element('div','answer-body');
    this.preview=element('div','citation-preview');this.preview.hidden=true;
    this.state=element('div','answer-state');this.el.append(this.body,this.preview,this.state);
    this.references={};this.fullText='';this.citations=new Map();this.blocks=[];this.pending=null;
  }
  citation(id) {
    const source=this.getSource(id);if(!this.citations.has(id))this.citations.set(id,this.citations.size+1);
    const n=this.citations.get(id),button=element('button','citation',String(n));button.type='button';button.title=source.title;
    button.setAttribute('aria-label',`Source ${n}: ${source.title}`);
    this.preview.id||=`citation-${++unique}`;button.setAttribute('aria-controls',this.preview.id);
    button.setAttribute('aria-expanded',String(!this.preview.hidden && this.preview.dataset.source===id));
    button.onclick=()=>{
      const open=this.preview.hidden || this.preview.dataset.source!==id;
      for(const b of this.body.querySelectorAll('.citation'))b.setAttribute('aria-expanded','false');
      this.preview.hidden=!open;button.setAttribute('aria-expanded',String(open));this.preview.dataset.source=id;
      if(open){
        const detail=new SourceDetails();detail.update(this.getSource(id));
        if(this.references[id]?.evidence==='discovered')detail.el.append(element('p','source-caution','This citation used a search result, not a page opened for this answer.'));
        const close=element('button','source-close','Close source preview');close.onclick=()=>{this.preview.hidden=true;button.setAttribute('aria-expanded','false');button.focus();};
        this.preview.replaceChildren(detail.el,close);
      }
    };
    return button;
  }
  update(answer,replaying=false) {
    this.references=answer.references || {};
    const text=answer.text || '';
    if(text.startsWith(this.fullText) || !this.fullText)this.fullText=text;
    const final=answer.status!=='running';
    if(replaying || final){cancelAnimationFrame(this.pending);this.pending=null;this.render();}
    else if(!this.pending)this.pending=requestAnimationFrame(()=>{this.pending=null;this.render();});
    this.el.dataset.status=answer.status;
    this.state.textContent=answer.status==='running'?'':answer.status==='failed'?'Answer interrupted · '+(answer.error || 'The provider did not finish.'):['cancelled','interrupted'].includes(answer.status)?'Stopped · partial answer':'';
    this.state.hidden=!this.state.textContent;
  }
  render() {
    const blocks=parseBlocks(this.fullText);
    // Reconcile by key: a block whose source text is unchanged keeps its DOM (and citation state).
    for(let n=0;n<blocks.length;n++){
      const prev=this.blocks[n];
      if(prev && prev.key===blocks[n].key)continue;
      const el=renderBlock(blocks[n],this);
      if(prev)this.body.replaceChild(el,prev.el);else this.body.append(el);
      this.blocks[n]={key:blocks[n].key,el};
    }
    while(this.blocks.length>blocks.length){const removed=this.blocks.pop();removed.el.remove();}
  }
}
/** Question answers belong to the question card; the composer can answer it too. */
export class QuestionCard {
  constructor(segment,onAnswer) {
    this.onAnswer=onAnswer;this.selected=new Set();this.custom=!(segment.options||[]).length;this.noteOpen=false;
    this.el=element('div','question-card msg assistant');this.el.setAttribute('role','group');
    this.text=element('div','question-text');this.list=element('div','question-options');
    this.response=element('textarea','question-response');this.response.rows=2;this.response.maxLength=8000;
    this.response.setAttribute('aria-label','Your answer');
    this.response.oninput=()=>this.updateSubmit();
    this.response.onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();this.send();}};
    this.actions=element('div','question-actions');this.hint=element('span','question-hint');
    this.answerLine=element('div','question-answer');this.answerLine.hidden=true;
    this.el.append(this.text,this.list,this.response,this.actions,this.answerLine);
    this.el.addEventListener('keydown',event=>{
      if(!this.open||event.target===this.response)return;
      const index=Number(event.key)-1,options=this.segment.options||[];
      if(Number.isInteger(index)&&index>=0&&index<options.length){event.preventDefault();this.choose(options[index].label);}
      else if(event.key==='Escape'&&(this.selected.size||this.custom&&options.length)){event.preventDefault();this.selected.clear();this.custom=false;this.noteOpen=false;this.render();}
    });
    this.update(segment);
  }
  get open(){return this.segment?.status==='running';}
  choose(label) {
    const multi=this.segment.multiSelect;
    if(multi){this.selected.has(label)?this.selected.delete(label):this.selected.add(label);this.render();return;}
    if(this.selected.has(label)&&!this.noteOpen){this.send();return;}  // second click confirms
    this.selected=new Set([label]);this.custom=false;this.render();
  }
  send() {
    if(!this.open)return;
    const text=(this.custom||this.noteOpen)?this.response.value.trim():'';
    if(!this.selected.size&&!text)return;
    this.onAnswer(this.segment.questionId,[...this.selected],text);
  }
  updateSubmit(){if(this.submit)this.submit.disabled=!(this.selected.size||((this.custom||this.noteOpen)&&this.response.value.trim()));}
  update(segment) {
    const signature=JSON.stringify([segment.question,segment.options,segment.multiSelect,segment.status,segment.answer]);
    if(signature===this.signature)return;this.signature=signature;this.segment=segment;this.render();
  }
  render() {
    const segment=this.segment,open=this.open,options=segment.options||[],multi=segment.multiSelect;
    this.text.textContent=segment.question||'';
    this.el.setAttribute('aria-label',segment.question||'Question');
    this.list.replaceChildren();this.actions.replaceChildren();
    options.forEach((option,index)=>{
      const button=element('button','question-option');button.type='button';button.disabled=!open;
      button.setAttribute('role',multi?'checkbox':'radio');
      const chosen=open?this.selected.has(option.label):(segment.answer?.selected||[]).includes(option.label);
      button.setAttribute('aria-checked',String(chosen));button.classList.toggle('chosen',chosen);
      const key=element('kbd','question-option-key',String(index+1));key.setAttribute('aria-hidden','true');
      const label=element('span','question-option-label',option.label);
      button.append(key,label);
      if(option.description)button.append(element('span','question-option-description',option.description));
      button.onclick=()=>{if(open)this.choose(option.label);};
      this.list.append(button);
    });
    const other=element('button','question-option question-other');other.type='button';other.disabled=!open;
    other.setAttribute('role',multi?'checkbox':'radio');
    const otherChosen=open?this.custom:!!segment.answer?.text&&!(segment.answer?.selected||[]).length;
    other.setAttribute('aria-checked',String(otherChosen));other.classList.toggle('chosen',otherChosen);
    other.append(element('span','question-option-label',options.length?'Something else…':'Type your answer'));
    other.onclick=()=>{if(!open)return;this.custom=multi?!this.custom:true;if(!multi)this.selected.clear();this.noteOpen=false;this.render();if(this.custom)this.response.focus();};
    this.list.append(other);
    const showResponse=open&&(this.custom||this.noteOpen);
    this.response.hidden=!showResponse;this.response.disabled=!open;
    this.response.placeholder=this.custom?'Type your answer':'Add a note (optional)';
    this.submit=null;
    if(open) {
      this.el.dataset.state='open';
      if(this.selected.size||this.custom) {
        this.submit=element('button','btn accent question-submit','Send answer');this.submit.type='button';this.submit.onclick=()=>this.send();
        this.actions.append(this.submit);
        if(this.selected.size&&!this.custom&&!this.noteOpen){
          const note=element('button','question-note','Add a note');note.type='button';
          note.onclick=()=>{this.noteOpen=true;this.render();this.response.focus();};this.actions.append(note);
        }
        this.hint.textContent=multi?'Choose any that apply, then send':this.noteOpen||this.custom?'Enter sends':'Click the choice again or press Send';
      } else {
        this.hint.textContent=options.length?`Pick an option${options.length<10?' (or press 1–'+options.length+')':''}, or reply in the message box below`:'Type your answer here or in the message box below';
      }
      this.actions.append(this.hint);this.updateSubmit();
    } else {
      this.el.dataset.state=segment.status;
      const answer=segment.answer?.summary;
      this.answerLine.hidden=!answer;this.answerLine.textContent=answer?`You answered: ${answer}`:'';
      if(!answer && segment.status!=='running'){this.answerLine.hidden=false;this.answerLine.textContent=segment.status==='interrupted'?'Question left open':'';}
    }
  }
}
export class AssistantTurn {
  constructor(turn,getSource) {
    this.el=element('article','assistant-turn');this.el.dataset.turnId=turn.id;
    this.user=element('div','msg user');this.identity=element('div','assistant-identity');this.identity.append(icon('cad'),element('span','','CADPilot'));
    this.content=element('div','turn-content');this.footer=element('div','turn-footer');
    this.el.append(this.user,this.identity,this.content,this.footer);this.components=new Map();this.getSource=getSource;
  }
  update(turn,replaying) {
    if(this.user.textContent!==turn.user)this.user.textContent=turn.user;
    this.user.hidden=!turn.user;this.el.dataset.status=turn.status;
    const ordered=[];
    let group=[];const renderGroup=()=>{
      if(!group.length)return;const id=`group:${group[0].id}`;let timeline=this.components.get(id);
      if(!timeline){timeline=new ActivityTimeline(this.getSource);this.components.set(id,timeline);this.content.append(timeline.el);}
      timeline.update(group,replaying);ordered.push(timeline.el);group=[];
    };
    for(const segment of turn.segments) {
      if(segment.kind==='research_agent') {
        renderGroup();let card=this.components.get(segment.id);
        if(!card){card=new ResearchCard();this.components.set(segment.id,card);}
        card.update({...segment.research,steps:segment.researchSteps,status:segment.status,started_at:segment.research?.started_at || segment.startedAt,
          finished_at:segment.research?.finished_at || segment.finishedAt,
          ...(['interrupted','cancelled','failed'].includes(segment.status)?{activity:({interrupted:'Research was interrupted',cancelled:'Research stopped',failed:'Research failed'})[segment.status]}:{})});
        card.el.dataset.operationId=segment.id;ordered.push(card.el);continue;
      }
      if(segment.kind==='question'){
        renderGroup();let card=this.components.get(segment.id);
        if(!card){card=new QuestionCard(segment,(id,selected,text)=>window.cadpilotAnswer?.(id,selected,text));this.components.set(segment.id,card);this.content.append(card.el);}
        card.update(segment);ordered.push(card.el);continue;
      }
      if(segment.kind!=='answer'){group.push(segment);continue;}
      renderGroup();let answer=this.components.get(segment.id);
      if(!answer){answer=new StreamingAnswer(this.getSource);this.components.set(segment.id,answer);this.content.append(answer.el);}
      answer.update(segment,replaying);ordered.push(answer.el);
    }
    renderGroup();
    // A drafted tool row can become a research card after its arguments arrive.
    // Reconcile its position without replacing surrounding answers/disclosures.
    let cursor=this.content.firstChild;
    for(const node of ordered){if(node===cursor)cursor=cursor.nextSibling;else this.content.insertBefore(node,cursor);}
    for(const [id,component] of this.components)if(!ordered.includes(component.el)){component.el.remove();this.components.delete(id);}
    this.footer.textContent=turn.status==='running'?'':turn.status==='completed'?'Response complete':turn.status==='paused'?'Waiting for your guidance':turn.status==='failed'?'Needs attention · '+(turn.reason || ''):turn.status==='interrupted'?(turn.interruption==='guidance'?'Updated with your guidance':'Interrupted · '+(turn.reason || 'previous work is preserved')):'Stopped · your work is preserved';
    this.footer.hidden=!this.footer.textContent;
    this.footer.title=turn.reason || '';
  }
  connection(connected){for(const component of this.components.values())component.connection?.(connected);}
  tick(){for(const component of this.components.values())component.tick?.();}
}
