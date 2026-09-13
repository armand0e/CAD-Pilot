/** Chat state is independent of desktop/session control state.
 * @typedef {'preparing'|'running'|'completed'|'failed'|'cancelled'|'interrupted'} Status
 * @typedef {{id:string, url:string, title:string, domain?:string, snippet?:string,
 * retrievedAt?:string, published_at?:string, opened:boolean, kind:string}} Source
 * @typedef {{id:string, turnId:string, kind:string, input:Object, status:Status,
 * startedAt:number, finishedAt?:number, order:number, result?:Object, error?:string}} Operation
 * @typedef {{id:string, turnId:string, kind:'answer', text:string, status:Status}} Answer
 * @typedef {Source & {kind:'search_result',snippet:string}} SearchResult
 * @typedef {Source & {kind:'page'|'pdf',requested_url?:string,excerpt?:string,headings?:string[]}} PageVisitResult
 * @typedef {Operation & {kind:'thinking',text:string}} DisplayableStatus
 * @typedef {{sourceId:string,turnId:string,operationId:string,evidence:'opened'|'discovered'}} SourceReference
 * @typedef {{id:string, user:string, startedAt:number, status:Status, segments:Array<Operation|Answer>}} AssistantTurn
 */
export const createChatState = () => ({turns: [], byId: new Map(), sources: new Map(), aliases: new Map(), seen: new Set(), serial: 0, current: null});
const terminal = s => ['completed', 'failed', 'cancelled', 'interrupted'].includes(s);
const phases = {awaiting_answer:'Waiting for your answer', planning:'Thinking…', thinking:'Choosing desktop actions', modeling:'Designing the model', building:'Checking geometry and exports', verifying:'Checking the result', waiting_screen:'Waiting for CAD', recovering:'Reviewing the approach'};
export const safeURL = value => {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u : null; } catch { return null; }
};
export function rememberSource(state, source) {
  const url = safeURL(source.url); if (!url || !source.id) return;
  const previousId = state.aliases.get(source.requested_url) || state.aliases.get(url.href);
  const previous = state.sources.get(previousId) || state.sources.get(source.id) || {};
  const opened = source.opened === true || (source.kind && source.kind !== 'search_result');
  const merged = {...previous, ...source, domain: url.hostname, opened: !!(previous.opened || opened)};
  if (previous.opened && !opened) {
    for (const key of ['title', 'url', 'kind', 'excerpt', 'retrievedAt', 'retrieved_at', 'published_at']) if (previous[key]) merged[key] = previous[key];
  }
  merged.snippet = source.snippet || previous.snippet || (source.kind === 'search_result' ? source.excerpt || source.text || '' : '');
  const ids = new Set([source.id, previousId, ...(previous.aliases || [])].filter(Boolean));
  merged.aliases = [...ids];
  for (const id of ids) state.sources.set(id, merged);
  state.aliases.set(url.href, source.id);
  if (source.requested_url) state.aliases.set(source.requested_url, source.id);
}
function finishThinking(turn, ts, status = 'completed') {
  for (const s of turn.segments) if (s.kind === 'thinking' && s.status === 'running') { s.status = status; s.finishedAt = ts; }
}
function segment(turn, id) { return turn.segments.find(s => s.id === id); }
function operation(turn, id, kind, event) {
  let op = segment(turn, id);
  if (!op) { op = {id, turnId:turn.id, kind, input:{}, status:'running', startedAt:event.ts, order:turn.segments.length}; turn.segments.push(op); }
  return op;
}
function answer(turn, id, event) {
  finishThinking(turn, event.ts);
  return operation(turn, id, 'answer', event);
}
function bindReferences(state, turn, answer) {
  answer.references ||= {};
  for(const match of (answer.text || '').matchAll(/\[(web_[a-f0-9]{12})\]/g)) {
    const source=state.sources.get(match[1]);
    if(source && !answer.references[match[1]])answer.references[match[1]]={sourceId:match[1],turnId:turn.id,
      operationId:source.operationId,evidence:source.opened?'opened':'discovered'};
  }
}
function settleTurn(turn, event, status) {
  turn.status = status; turn.finishedAt = event.ts;
  finishThinking(turn, event.ts, status === 'completed' ? 'completed' : status);
  for (const s of turn.segments) if (s.status === 'running' || s.status === 'preparing') {
    s.status = status === 'completed' ? 'interrupted' : status; s.finishedAt = event.ts;
  }
}

/** Idempotent append/update reducer. Cancelled operations never accept late success. */
export function reduceChat(state, event) {
  // Steering acknowledgments are control events, not model thought or replies.
  // Also hide the canned resume acknowledgment in transcripts from older workers.
  if(event.t==='guidance' || (event.t==='assistant' && event.presentation==='status' &&
      event.message==='Continuing from the current document with your latest guidance.'))return false;
  if(!['user','control','phase','thinking_start','thinking_delta','thinking_done','thinking_truncated','tool_input_start','tool_input_delta','tool_input_done','tool_settled','research_start','research_result',
    'research_error','research_cancelled','research_notes','intent','action','native_attempt','step_done','step_review',
    'tool_error','step_error','step_blocked','step_superseded','answer_start','answer_delta','answer_done','assistant',
    'pause','note','recovery','native_review','task_review','done','error','question','answer'].includes(event.t))return false;
  const eventKey = event.event_id || (event.id ? `legacy:${event.id}` : null);
  if (eventKey && state.seen.has(eventKey)) return false;
  if (eventKey) state.seen.add(eventKey);
  const serial = ++state.serial;
  let turnId = event.turn_id || state.current || `legacy-turn:${eventKey || serial}`;
  if (event.t === 'user') {
    turnId = event.turn_id || `legacy-turn:${eventKey || serial}`;
    if (state.current && state.current !== turnId) {
      const previous = state.byId.get(state.current);
      if (previous?.status === 'running') {settleTurn(previous, event, 'interrupted');previous.interruption='guidance';}
    }
    state.current = turnId;
  }
  if (!state.byId.has(turnId)) {
    if (['mode', 'web_setting', 'artifact'].includes(event.t) || (event.t === 'phase' && event.phase === 'idle')) return true;
    const turn = {id:turnId, user:'', startedAt:event.ts, status:'running', segments:[]};
    state.byId.set(turnId, turn); state.turns.push(turn); state.current = turnId;
  }
  const turn = state.byId.get(turnId);
  const id = eventKey || `event:${serial}`;
  const stepId = event.operation_id || `${turn.id}:cad:${event.step}`;
  const recent = kind => [...turn.segments].reverse().find(s => s.kind === kind && s.status === 'running');
  switch (event.t) {
    case 'user': turn.user = event.text; break;
    case 'control': if (event.locked) { turn.status = 'running'; } else if (turn.status === 'running') settleTurn(turn,event,'interrupted'); break;
    case 'phase': {
      if(event.timeline===false)break; // Actual model request owns its activity row.
      if (!phases[event.phase] || terminal(turn.status) || recent('cad') || recent('search') || recent('read') || turn.segments.some(s=>s.status==='preparing')) break;
      const previous = recent('thinking');
      if (previous?.phase === event.phase) break;
      finishThinking(turn,event.ts);
      Object.assign(operation(turn,`thinking:${id}`,'thinking',event), {label:phases[event.phase], phase:event.phase, text:''}); break;
    }
    case 'thinking_start': {
      if(terminal(turn.status) || segment(turn,event.operation_id))break;
      finishThinking(turn,event.ts);
      Object.assign(operation(turn,event.operation_id || id,'thinking',event), {label:event.label || 'Thinking…',text:event.text || '',pending:new Map(),requestId:event.request_id}); break;
    }
    case 'thinking_delta': {
      const op = segment(turn,event.operation_id);
      if(op?.status==='running') {
        if(event.offset===undefined)op.text+=event.text || '';
        else {
          const length=[...op.text].length;
          if(event.offset>=length)op.pending.set(event.offset,event.text || '');
          while(op.pending.has([...op.text].length)) {const offset=[...op.text].length;const delta=op.pending.get(offset);op.pending.delete(offset);if(!delta)break;op.text+=delta;}
        }
      } break;
    }
    case 'thinking_truncated': {const op=segment(turn,event.operation_id);if(op)op.truncated=true;break;}
    case 'thinking_done': {
      const op = segment(turn,event.operation_id); if (op?.status === 'running' || (op?.status==='interrupted' && event.status==='cancelled')) Object.assign(op,{status:event.status || 'completed',finishedAt:event.ts,error:event.message}); break;
    }
    case 'tool_input_start': {
      if(terminal(turn.status) || segment(turn,event.operation_id))break;
      finishThinking(turn,event.ts);
      Object.assign(operation(turn,event.operation_id,'proposal',event),{status:'preparing',label:'Preparing CAD operation',rawInput:'',pending:new Map(),requestId:event.request_id});break;
    }
    case 'tool_input_delta': {
      const op=segment(turn,event.operation_id);if(op?.status!=='preparing')break;
      if(event.offset>=[...op.rawInput].length)op.pending.set(event.offset,event.text);
      while(op.pending.has([...op.rawInput].length)){const offset=[...op.rawInput].length;const delta=op.pending.get(offset);op.pending.delete(offset);if(!delta)break;op.rawInput+=delta;}
      if(event.tool)op.label='Preparing '+event.tool.replaceAll('_',' ');
      break;
    }
    case 'tool_input_done': {const op=segment(turn,event.operation_id);if(op?.status==='preparing')op.inputComplete=true;break;}
    case 'tool_settled': {
      const op=segment(turn,event.operation_id);if(op && (!terminal(op.status) || (op.status==='interrupted' && event.status==='cancelled'))){op.status=event.status;op.finishedAt=event.ts;op.result={message:event.message};if(op.status==='failed')op.error=event.message;}break;
    }
    case 'research_start': {
      finishThinking(turn,event.ts);
      const kind = event.operation === 'read' || /^https?:/.test(event.query) ? 'read' : 'search';
      const op=operation(turn,event.operation_id || `web:${id}`,kind,event);
      if(terminal(op.status))break;
      Object.assign(op, {kind,status:'running',executingAt:event.ts,input:{query:event.query,focus:event.focus},label:event.query}); break;
    }
    case 'research_result': case 'research_error': case 'research_cancelled': {
      let op = event.operation_id ? segment(turn,event.operation_id) : [...turn.segments].reverse().find(s => ['search','read'].includes(s.kind) && s.status === 'running');
      if (!op) op = operation(turn,event.operation_id || `web:${id}`,event.operation === 'read' ? 'read' : 'search',event);
      if (op.status==='interrupted' && event.t==='research_cancelled')op.status='running';
      if (terminal(op.status)) break;
      op.status = event.t === 'research_result' ? 'completed' : event.t === 'research_error' ? 'failed' : 'cancelled';
      op.finishedAt = event.ts; op.error = event.message; op.result = event;
      if (event.operation) op.kind = event.operation === 'read' ? 'read' : 'search';
      for (const source of event.sources || []) rememberSource(state,{...source,operationId:op.id});
      break;
    }
    case 'research_notes': break; // Already available in the saved Model panel; never fabricate thinking from extracted facts.
    case 'intent': {
      finishThinking(turn,event.ts);
      const op = operation(turn,stepId,'cad',event);
      if (terminal(op.status)) break;
      Object.assign(op, {kind:'cad',status:'running',executingAt:event.ts,label:event.text, input:{objective:event.text,expected_result:event.expected_result,tool:event.tool,arguments:event.arguments}, actions:op.actions || []}); break;
    }
    case 'action': { const op=segment(turn,stepId); if (op?.status === 'running') op.actions.push(event.action); break; }
    case 'native_attempt': { const op=recent('cad'); if(op) op.attempt=event.attempt; break; }
    case 'step_done': {
      const op=segment(turn,stepId); if(op?.status === 'running') Object.assign(op,{status:'completed',finishedAt:event.ts,result:event}); break;
    }
    case 'step_review': { const op=segment(turn,stepId); if(op) op.review=event; break; }
    case 'tool_error': {
      let op=segment(turn,stepId);
      if (!op) { finishThinking(turn,event.ts); op=operation(turn,`cad-error:${id}`,'cad',event); op.status='failed'; op.finishedAt=event.ts; op.label='CAD operation rejected'; }
      op.errors ||= []; op.errors.push({attempt:event.attempt,message:event.message}); break;
    }
    case 'step_error': case 'step_blocked': case 'step_superseded': {
      const op=segment(turn,stepId); if (op?.status === 'running') Object.assign(op,{status:event.t === 'step_superseded' ? 'cancelled' : 'failed',finishedAt:event.ts,error:event.message}); break;
    }
    case 'answer_start': if(!segment(turn,event.answer_id))Object.assign(answer(turn,event.answer_id,event),{text:'', pending:new Map()}); break;
    case 'answer_delta': {
      const a=segment(turn,event.answer_id); if (!a || terminal(a.status)) break;
      a.pending.set(event.offset,event.text);
      let length=[...a.text].length;
      while(a.pending.has(length)) { a.text+=a.pending.get(length); a.pending.delete(length); length=[...a.text].length; }
      bindReferences(state,turn,a);
      break;
    }
    case 'answer_done': {
      const a=segment(turn,event.answer_id);
      if(a?.status === 'running' || (a?.status==='interrupted' && event.status==='cancelled'))Object.assign(a,{status:event.status || 'completed',finishedAt:event.ts,error:event.message}); break;
    }
    case 'assistant': case 'pause': {
      if(event.t==='pause' && !event.paused){turn.status='running';break;}
      const text=event.t === 'pause' ? event.reason : event.message;
      if(!text || (event.t === 'pause' && !event.paused)) break;
      if(event.t==='pause'){finishThinking(turn,event.ts);turn.status='paused';}
      if(turn.segments.some(s => s.kind === 'answer' && s.text === text)) break;
      if(event.presentation === 'status') {
        const op=recent('thinking') || operation(turn,`status:${id}`,'thinking',event);
        op.text=text; op.label='Working on your request'; break;
      }
      const a=Object.assign(answer(turn,`answer:${id}`,event),{text,status:'completed',finishedAt:event.ts});bindReferences(state,turn,a);
      if(event.t === 'pause') { finishThinking(turn,event.ts); turn.status='paused'; }
      break;
    }
    case 'note': case 'recovery': case 'native_review': case 'task_review': {
      const text=event.message || event.summary || event.observation; if(!text) break;
      const op=operation(turn,`status:${id}`,'thinking',event);
      Object.assign(op,{label:event.t.includes('review') ? 'Reviewing the result' : 'Status update',text,status:'completed',finishedAt:event.ts}); break;
    }
    case 'question': {
      if(segment(turn,`question:${event.question_id}`))break;
      finishThinking(turn,event.ts);
      Object.assign(operation(turn,`question:${event.question_id}`,'question',event),{questionId:event.question_id,question:event.question,
        options:event.options || [],multiSelect:!!event.multi_select,status:'running'});
      break;
    }
    case 'answer': {
      const q=[...state.turns].reverse().flatMap(t=>t.segments).find(s=>s.kind==='question' && s.questionId===event.question_id);
      if(q && !terminal(q.status)){q.status='completed';q.finishedAt=event.ts;q.answer={selected:event.selected || [],text:event.text || '',summary:event.summary || ''};}
      break;
    }
    case 'done': {
      // Older workers emit a success event even after a provider error or a
      // thinking-only response. Preserve the failure when replaying that log.
      if(turn.status==='failed')break;
      const lastAnswer = turn.segments.findLastIndex(s=>s.kind==='answer' && s.text?.trim());
      const unansweredThinking = turn.segments.slice(lastAnswer+1).some(s=>s.kind==='thinking' && s.id.startsWith('thinking-') && s.text?.trim());
      if(event.reason==='Reply sent; the model stays open for more changes.' && (lastAnswer===-1 || unansweredThinking)) {
        turn.reason='The model stopped without an answer. Send a message to continue.';
        settleTurn(turn,event,'interrupted'); break;
      }
      turn.reason=event.reason; settleTurn(turn,event,/stopped|cancelled/i.test(event.reason) ? 'cancelled' : 'completed'); break;
    }
    case 'error': turn.reason=event.message; settleTurn(turn,event,'failed'); break;
  }
  return true;
}
