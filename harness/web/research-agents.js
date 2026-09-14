/** One persistent research workspace at its invocation in the conversation. */
import {safeURL} from './chat/state.js';

const labels = {running:'Running', completed:'Complete', incomplete:'Needs follow-up', failed:'Failed', cancelled:'Stopped', interrupted:'Interrupted'};
const element = (tag, className, text) => {const node=document.createElement(tag);node.className=className;if(text)node.textContent=text;return node;};
const setText = (node, text) => {if(node.textContent!==text)node.textContent=text;};
const duration = seconds => {const n=Math.max(0,Math.floor(seconds||0));return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;};
const count = (n, one, many=one+'s') => `${n||0} ${n===1?one:many}`;
const unit = {mm:'mm', deg:'°', count:'×'};
let serial=0;

export class ResearchCard {
  constructor() {this.connected=true;}
  connection(connected) {this.connected=connected;if(this.card){this.status();this.tick();}}
  create(event) {
    const node=element('details','research-agent');node.open=event.status==='running';
    const summary=element('summary','research-agent-summary');
    const heading=element('span','research-agent-heading');
    const mark=element('span','research-agent-mark');mark.setAttribute('aria-hidden','true');
    mark.innerHTML='<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg>';
    const title=element('span','research-agent-title','Dimension researcher');
    const badge=element('span','research-agent-badge');
    const elapsed=element('span','research-agent-elapsed');
    heading.append(mark,title,badge,elapsed);
    const task=element('span','research-agent-task');
    const line=element('span','research-agent-line');
    const pulse=element('span','research-agent-pulse');pulse.setAttribute('aria-hidden','true');
    const activity=element('span','research-agent-activity');activity.setAttribute('role','status');
    line.append(pulse,activity);
    const detail=element('span','research-agent-detail');
    const counts=element('span','research-agent-counts');
    const wait=element('span','research-agent-wait');wait.hidden=true;
    const stop=element('button','research-agent-stop','Stop research');stop.type='button';stop.title='Stop this investigation; the assistant continues with what it already knows';
    stop.onclick=event=>{event.preventDefault();event.stopPropagation();stop.disabled=true;window.cadpilotCancelResearch?.(this.card.value?.agent_id);};
    summary.append(heading,task,line,detail,counts,wait,stop);

    const body=element('div','research-agent-body');
    const tabs=element('div','research-agent-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Dimension research');
    const panes={},buttons={},id=`dimension-research-${++serial}`;
    for(const [key,label] of [['activity','Activity'],['sources','Sources'],['findings','Findings']]) {
      const button=element('button','research-agent-tab',label);button.type='button';button.id=`${id}-${key}-tab`;
      button.setAttribute('role','tab');button.setAttribute('aria-controls',`${id}-${key}`);
      button.onclick=()=>{this.selectedByUser=true;this.select(key);};
      button.onkeydown=event=>{
        const keys=Object.keys(buttons),i=keys.indexOf(key);
        const next=event.key==='ArrowRight'?(i+1)%keys.length:event.key==='ArrowLeft'?(i+keys.length-1)%keys.length:event.key==='Home'?0:event.key==='End'?keys.length-1:null;
        if(next===null)return;event.preventDefault();this.selectedByUser=true;this.select(keys[next]);buttons[keys[next]].focus();
      };
      const pane=element('div','research-agent-pane');pane.id=`${id}-${key}`;pane.tabIndex=0;
      pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby',button.id);
      buttons[key]=button;panes[key]=pane;tabs.append(button);
    }
    const scope=element('div','research-agent-scope');
    const goals=element('ul','research-agent-goals');scope.append(element('h4','','Investigating'),goals);
    const steps=element('ol','research-agent-steps');steps.setAttribute('aria-label','Research activity');
    panes.activity.append(scope,steps);
    const sourceEmpty=element('p','research-agent-empty');
    const sources=element('ul','research-agent-sources');panes.sources.append(sourceEmpty,sources);
    const documented=element('p','research-agent-documented');
    const outcome=element('p','research-agent-outcome');
    const table=element('div','research-agent-findings');table.setAttribute('role','table');table.setAttribute('aria-label','Documented dimensions');
    const unresolved=element('div','research-agent-unresolved');
    const unknowns=element('ul','research-agent-unknowns');unresolved.append(element('h4','','Still unresolved'),unknowns);
    const assumed=element('div','research-agent-assumed');
    const assumptions=element('ul','research-agent-assumptions');assumed.append(element('h4','','Assumptions and readings to confirm'),assumptions);
    panes.findings.append(documented,outcome,table,unresolved,assumed);
    body.append(tabs,...Object.values(panes));node.append(summary,body);
    this.card={node,badge,task,activity,elapsed,detail,counts,wait,stop,panes,buttons,scope,goals,steps,sources,sourceEmpty,documented,outcome,table,unresolved,unknowns,assumed,assumptions};
    this.stepRows=[];this.sourceRows=new Map();this.el=node;
    this.select(event.status==='running'?'activity':'findings');
  }
  select(key) {
    this.selected=key;
    for(const [name,pane] of Object.entries(this.card.panes)) {
      pane.hidden=name!==key;const button=this.card.buttons[name];
      button.setAttribute('aria-selected',String(name===key));button.tabIndex=name===key?0:-1;
    }
  }
  update(event) {
    if(!this.card)this.create(event);
    const card=this.card,previous=card.value;
    card.value=event;card.node.dataset.agentId=event.agent_id||'';
    setText(card.task,event.task||'Investigating dimensions');
    setText(card.detail,event.detail||'');card.detail.hidden=!event.detail||event.status!=='running';
    const goalKey=JSON.stringify(event.dimensions||[]);
    if(goalKey!==this.goalKey){this.goalKey=goalKey;card.goals.replaceChildren(...(event.dimensions||[]).map(text=>element('li','',text)));}
    card.scope.hidden=!(event.dimensions||[]).length;

    // Sources accumulate: a later event never removes a link the user already saw.
    const cited=new Set((event.findings||[]).map(f=>f.source_id).filter(Boolean));
    for(const source of event.sources||[]) {
      const url=safeURL(source.url);if(!url)continue;
      const key=source.id||url.href;let row=this.sourceRows.get(key);
      if(!row){
        const node=element('li','research-agent-source');
        const link=element('a','');link.target='_blank';link.rel='noopener noreferrer';
        const name=element('span','research-agent-source-title'),domain=element('span','research-agent-source-domain');
        link.append(name,domain);const badge=element('small','');node.append(link,badge);
        row={node,link,name,domain,badge,kind:''};this.sourceRows.set(key,row);card.sources.append(node);
      }
      row.link.href=url.href;setText(row.name,source.title||url.hostname);setText(row.domain,url.hostname);
      row.kind=source.cited||cited.has(source.id)?'cited':['page','pdf'].includes(source.kind)?'read':'found';
      setText(row.badge,{cited:'Cited',read:'Read',found:'Found'}[row.kind]);row.node.dataset.kind=row.kind;
    }
    const order={cited:0,read:1,found:2};
    const sorted=[...this.sourceRows.values()].sort((a,b)=>order[a.kind]-order[b.kind]);
    // Reorder only when needed and never while the user is focused inside the list.
    if(!card.sources.contains(document.activeElement)&&sorted.some((row,i)=>card.sources.children[i]!==row.node))for(const row of sorted)card.sources.append(row.node);
    const sourceCount=this.sourceRows.size,read=[...this.sourceRows.values()].filter(r=>r.kind!=='found').length;
    // Search leads are listed under Sources; the summary line counts real work.
    setText(card.counts,`${count(event.searches,'search','searches')} · ${count(event.pages_read??read,'page')} read${Number.isInteger(event.calls)?` · ${count(event.calls,'tool call')}`:` · ${count(sourceCount,'source')}`}`);
    setText(card.buttons.sources,`Sources${sourceCount?` (${sourceCount})`:''}`);
    card.sourceEmpty.hidden=!!sourceCount;
    setText(card.sourceEmpty,event.status==='running'?'Sources will appear here as the researcher finds them.':'No web sources were recorded for this investigation.');

    const pane=card.panes.activity,follow=!pane.hidden&&pane.scrollHeight-pane.scrollTop-pane.clientHeight<40;
    const steps=event.steps?.length?event.steps:[{at:event.ts||event.started_at,activity:event.activity||'Starting research',detail:event.detail||''}];
    for(let i=0;i<steps.length;i++) {
      let row=this.stepRows[i];
      if(!row){
        const node=element('li','research-agent-step'),time=element('span','research-agent-step-time');
        const text=element('div',''),title=element('span','research-agent-step-title'),detail=element('span','research-agent-step-detail');
        text.append(title,detail);node.append(time,text);row={node,time,title,detail};this.stepRows.push(row);card.steps.append(node);
      }
      const step=steps[i];setText(row.time,duration(step.at-event.started_at));setText(row.title,step.activity);
      setText(row.detail,step.detail||'');row.detail.hidden=!step.detail;
      row.node.classList.toggle('is-current',i===steps.length-1&&event.status==='running');
    }
    while(this.stepRows.length>steps.length)this.stepRows.pop().node.remove();
    if(follow)pane.scrollTop=pane.scrollHeight;

    const findings=event.findings||[];
    const documentedCount=Number.isInteger(event.documented_dimensions)?event.documented_dimensions:findings.filter(f=>f.evidence!=='drawing_image').length;
    const visualCount=Number.isInteger(event.visual_dimensions)?event.visual_dimensions:findings.filter(f=>f.evidence==='drawing_image').length;
    card.documented.hidden=event.status==='running'||!(Number.isInteger(event.documented_dimensions)||findings.length);
    setText(card.documented,`${count(documentedCount,'dimension')} cited from text${visualCount?` · ${count(visualCount,'drawing reading')} to confirm`:''}`);
    setText(card.outcome,event.summary||(event.status==='running'?'Findings will appear here when the investigation finishes.':event.status==='completed'?'The investigation has finished.':event.status==='incomplete'?'The investigation finished with unresolved questions.':'The investigation ended before final findings were returned.'));
    const findingKey=JSON.stringify(findings);
    if(findingKey!==this.findingKey){
      this.findingKey=findingKey;card.table.replaceChildren();
      if(findings.length){
        const head=element('div','research-agent-finding is-head');head.setAttribute('role','row');
        for(const text of ['Dimension','Value','Datum','Evidence']){const cell=element('span','',text);cell.setAttribute('role','columnheader');head.append(cell);}
        card.table.append(head);
      }
      for(const f of findings){
        const row=element('div','research-agent-finding');row.setAttribute('role','row');
        const name=element('span','research-agent-finding-name',f.name||'');name.setAttribute('role','cell');
        const value=element('span','research-agent-finding-value',`${f.value}${f.unit==='mm'?' mm':unit[f.unit]||''}`);value.setAttribute('role','cell');
        const datum=element('span','research-agent-finding-datum',f.datum||'');datum.setAttribute('role','cell');
        const evidence=element('span','research-agent-finding-evidence');evidence.setAttribute('role','cell');
        const source=(event.sources||[]).find(s=>s.id===f.source_id),url=source&&safeURL(source.url);
        if(f.evidence==='drawing_image'){const tag=element('small','research-agent-tag is-visual',`Drawing${f.page?` p.${f.page}`:''}`);tag.title='Read from a rendered drawing page; confirm before use';evidence.append(tag);}
        else{const tag=element('small','research-agent-tag','Quoted');tag.title='Quote matched the opened document';evidence.append(tag);}
        if(url){const link=element('a','',url.hostname.replace(/^www\./,''));link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';evidence.append(link);}
        row.append(name,value,datum,evidence);card.table.append(row);
      }
    }
    const unknownKey=JSON.stringify(event.unknowns||[]);
    if(unknownKey!==this.unknownKey){this.unknownKey=unknownKey;card.unknowns.replaceChildren(...(event.unknowns||[]).map(text=>element('li','',text)));}
    card.unresolved.hidden=!(event.unknowns||[]).length;
    const assumedKey=JSON.stringify(event.assumptions||[]);
    if(assumedKey!==this.assumedKey){this.assumedKey=assumedKey;card.assumptions.replaceChildren(...(event.assumptions||[]).map(text=>element('li','',text)));}
    card.assumed.hidden=!(event.assumptions||[]).length;
    setText(card.buttons.findings,`Findings${findings.length?` (${findings.length})`:''}`);
    if(previous?.status==='running'&&event.status!=='running'&&!this.selectedByUser)this.select('findings');
    this.status();this.tick();
  }
  status() {
    const card=this.card,running=card.value.status==='running';
    card.node.dataset.status=card.value.status;card.node.classList.toggle('is-live',running&&this.connected);
    card.stop.hidden=!(running&&this.connected&&card.value.agent_id);
    setText(card.badge,running&&!this.connected?'Reconnecting':labels[card.value.status]||'Unknown');
    setText(card.activity,running&&!this.connected?'Connection lost · research status will refresh':card.value.activity||'');
  }
  tick() {
    const card=this.card;if(!card)return;
    const value=card.value,now=Date.now()/1000,end=value.finished_at||now,elapsed=duration(end-value.started_at);
    if(card.elapsed.textContent!==elapsed){setText(card.elapsed,elapsed);card.elapsed.setAttribute('aria-label',`Elapsed ${elapsed}`);}
    const age=now-(value.ts||value.started_at);
    card.wait.hidden=value.status!=='running'||!this.connected||age<45;
    if(!card.wait.hidden)setText(card.wait,`Last activity update ${duration(age)} ago`);
  }
}
