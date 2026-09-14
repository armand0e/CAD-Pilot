/** Child research activity stays visible outside the scrolling conversation. */
const labels = {running:'Running', completed:'Complete', incomplete:'Needs follow-up', failed:'Failed', cancelled:'Stopped', interrupted:'Interrupted'};
const element = (tag, className, text) => {const node=document.createElement(tag);node.className=className;if(text)node.textContent=text;return node;};
const duration = seconds => {const n=Math.max(0,Math.floor(seconds||0));return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;};
const count = (n, one, many=one+'s') => `${n||0} ${n===1?one:many}`;

export class ResearchAgents {
  constructor(root) {
    this.root=root;this.cards=new Map();this.connected=true;
    this.timer=setInterval(()=>this.tick(),1000);
  }
  reset() {this.cards.clear();this.root.replaceChildren();this.root.hidden=true;}
  restore(events, connected=true) {
    const expanded=new Set([...this.cards].filter(([,card])=>card.node.open).map(([id])=>id));
    this.reset();this.connected=connected;for(const event of events||[])this.consume(event);
    for(const [id,card] of this.cards)card.node.open=expanded.has(id);
  }
  connection(connected) {this.connected=connected;for(const card of this.cards.values())this.status(card);}
  consume(event) {
    if(event.t!=='research_agent'||!event.agent_id)return;
    let card=this.cards.get(event.agent_id);
    if(card?.value.ts>event.ts)return;
    if(!card) {
      const node=element('details','research-agent');node.dataset.agentId=event.agent_id;
      const summary=element('summary','research-agent-summary');
      const heading=element('span','research-agent-heading');
      const title=element('span','research-agent-title','Dimension researcher');
      const mark=element('span','research-agent-mark');mark.setAttribute('aria-hidden','true');
      mark.innerHTML='<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg>';
      const badge=element('span','research-agent-badge');heading.append(mark,title,badge);
      const task=element('span','research-agent-task');
      const line=element('span','research-agent-line');const activity=element('span','research-agent-activity');
      activity.setAttribute('role','status');const elapsed=element('span','research-agent-elapsed');line.append(activity,elapsed);
      summary.append(heading,task,line);
      const body=element('div','research-agent-body');
      const detail=element('p','research-agent-detail');const goals=element('p','research-agent-goals');
      const counts=element('p','research-agent-counts');const sources=element('ul','research-agent-sources');
      const outcome=element('p','research-agent-outcome');const unknowns=element('ul','research-agent-unknowns');
      body.append(detail,goals,counts,sources,outcome,unknowns);node.append(summary,body);
      card={node,badge,task,activity,elapsed,detail,goals,counts,sources,outcome,unknowns};
      this.cards.set(event.agent_id,card);this.root.prepend(node);
      // Keep recent completed work without allowing old cards to fill the sidebar.
      if(this.cards.size>12)for(const [id,old] of this.cards){if(old!==card&&old.value.status!=='running'){old.node.remove();this.cards.delete(id);break;}}
    }
    card.value=event;this.root.hidden=false;
    card.task.textContent=event.task||'Investigating dimensions';
    card.detail.textContent=event.detail||'';card.detail.hidden=!event.detail;
    card.goals.textContent=(event.dimensions||[]).join(' · ');
    card.counts.textContent=`${count(event.searches,'search','searches')} · ${count(event.pages_read,'page')} read · ${count((event.sources||[]).length,'source')} found`;
    const sourceKey=JSON.stringify(event.sources||[]);
    if(sourceKey!==card.sourceKey){
      card.sourceKey=sourceKey;card.sources.replaceChildren();
      for(const source of event.sources||[]){
        const row=element('li','');let url;
        try{url=new URL(source.url);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)continue;}catch{continue;}
        const link=element('a','',source.title||url.hostname);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
        row.append(link,element('small','', ['page','pdf'].includes(source.kind)?'Read':'Found'));card.sources.append(row);
      }
    }
    card.outcome.textContent=event.summary||'';card.outcome.hidden=!event.summary;
    card.unknowns.replaceChildren();for(const text of event.unknowns||[])card.unknowns.append(element('li','',text));
    this.status(card);this.tick();
  }
  status(card) {
    const running=card.value.status==='running';
    card.node.dataset.status=card.value.status;card.node.classList.toggle('is-live',running&&this.connected);
    card.badge.textContent=running&&!this.connected?'Reconnecting':labels[card.value.status]||'Unknown';
    const text=running&&!this.connected?'Connection lost · research status will refresh':card.value.activity||'';
    if(card.activity.textContent!==text)card.activity.textContent=text;
  }
  tick() {
    for(const card of this.cards.values()){
      const value=card.value,end=value.finished_at||Date.now()/1000;
      card.elapsed.textContent=duration(end-value.started_at);card.elapsed.setAttribute('aria-label',`Elapsed ${duration(end-value.started_at)}`);
    }
  }
}
