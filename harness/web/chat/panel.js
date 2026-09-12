import {createChatState,reduceChat} from './state.js';
import {AssistantTurn,element} from './components.js';

export class ChatPanel {
  constructor(root,jump,announcer) {
    this.root=root;this.jump=jump;this.announcer=announcer;this.state=createChatState();this.views=new Map();this.follow=true;root.dataset.follow='true';
    root.addEventListener('scroll',()=>{
      this.follow=root.scrollHeight-root.scrollTop-root.clientHeight<65;this.jump.hidden=this.follow;root.dataset.follow=String(this.follow);
    },{passive:true});
    // Expanding history/source details is a reading action, never a scroll-to-end trigger.
    root.addEventListener('click',event=>{if(event.target.closest('[aria-expanded]')){this.follow=false;root.dataset.follow='false';this.jump.hidden=false;}},true);
    jump.onclick=()=>{this.follow=true;root.dataset.follow='true';root.scrollTo({top:root.scrollHeight,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion==='reduced'?'instant':'smooth'});jump.hidden=true;};
    this.observer=new ResizeObserver(()=>{if(this.follow)this.pin();});this.observer.observe(root);
  }
  reset(){this.observer.disconnect();this.observer.observe(this.root);this.state=createChatState();this.views.clear();this.follow=true;this.root.dataset.follow='true';this.pending?.remove();this.pending=null;this.jump.hidden=true;}
  optimistic(text){this.pending?.remove();this.pending=element('div','msg user pending-user',text);this.pending.setAttribute('aria-label','Submitting your message');this.root.querySelector('#chat-empty')?.remove();this.root.append(this.pending);this.follow=true;this.root.dataset.follow='true';this.pin();}
  reject(){this.pending?.remove();this.pending=null;}
  pin(){cancelAnimationFrame(this.scrollFrame);this.scrollFrame=requestAnimationFrame(()=>{if(this.follow)this.root.scrollTop=this.root.scrollHeight;});}
  consume(event,{replaying=false}={}) {
    const previous=this.state.current;
    const anchor=!this.follow ? [...this.root.querySelectorAll('.timeline-entry:not([hidden]),.answer-body,.msg.user')].find(el=>el.getBoundingClientRect().bottom>this.root.getBoundingClientRect().top) : null;
    const anchorTop=anchor?.getBoundingClientRect().top;
    if(!reduceChat(this.state,event))return;
    if(event.t==='user')this.reject();
    if(!this.state.turns.length)return;
    this.root.querySelector('#chat-empty')?.remove();
    const affected=event.t==='research_result' ? this.state.turns : [...new Set([previous,event.turn_id || this.state.current])].map(id=>this.state.byId.get(id)).filter(Boolean);
    for(const turn of affected) {
      let view=this.views.get(turn.id);
      if(!view){view=new AssistantTurn(turn,id=>this.state.sources.get(id));this.views.set(turn.id,view);this.root.append(view.el);this.observer.observe(view.el);}
      view.update(turn,replaying);
    }
    if(anchor?.isConnected && !this.follow)this.root.scrollTop+=anchor.getBoundingClientRect().top-anchorTop;
    if(!replaying && ['research_start','research_result','research_error','research_cancelled','answer_start','answer_done','done','error','pause'].includes(event.t)) {
      this.announcer.textContent=({research_start:'Web lookup started',research_result:'Web lookup complete',research_error:'Web lookup failed',research_cancelled:'Web lookup cancelled',answer_start:'Answer arriving',answer_done:'Answer '+(event.status || 'complete'),done:event.reason,error:event.message,pause:event.reason})[event.t] || '';
    }
    if(event.persistence_warning)this.announcer.textContent=event.persistence_warning;
    if(this.follow)this.pin();else this.jump.hidden=false;
  }
  replay(events,active) {
    for(const event of events)reduceChat(this.state,event);
    if(this.state.turns.length)this.root.querySelector('#chat-empty')?.remove();
    this.reject();
    if(!active) {
      // A process can exit mid-tool. Reopening must not restart its animation or
      // turn it into a success. Existing explicitly terminal records stay intact.
      for(const turn of this.state.turns)if(turn.status==='running') {
        turn.status='interrupted';
        for(const op of turn.segments)if(op.status==='running' || op.status==='preparing')op.status='interrupted';
      }
    }
    for(const turn of this.state.turns){
      let view=this.views.get(turn.id);
      if(!view){view=new AssistantTurn(turn,id=>this.state.sources.get(id));this.views.set(turn.id,view);this.root.append(view.el);this.observer.observe(view.el);}
      view.update(turn,true);
    }
    if(this.follow)this.pin();
  }
}
