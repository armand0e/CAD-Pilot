import {ChatPanel} from './chat/panel.js';
import {icon} from './chat/components.js';
import { WorkspaceEditor } from './workspace-editor.js';

// CADPilot frontend — session tabs, resilient streams, agent timeline, action overlay.

const $ = (id) => document.getElementById(id);
const state = {
  sessions: [], session: null, viewWS: null, agentWS: null,
  mode: "auto", locked: false, running: false,
  latestFrame: null, decoding: false, firstFrame: false, awaiting: false,
  agentConnected: false, viewConnected: false, pending: false, paused: false,
  completed: 0, maxSteps: 40, startedAt: null, endedAt: null, replaying: false,
  lastEvent: 0, generation: 0, restored: false, health: {}, phase: "idle",
  freshTask: false, canContinue: false, pendingText: "",
  project: null, projectBusy: false,
  webSources: {}, researchCalls: 0, nativeOperation: false, nativeAttempt: 0, nativeParent: null,
};
const emptyChat = $("chat").innerHTML;
const chatPanel = new ChatPanel($('chat'), $('jump-latest'), $('chat-announcer'));
const escapeHTML = (text) => String(text).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function syncControls() {
  const typed = !!$("composer-input").value.trim();
  const stopMode = state.running && state.agentConnected && !typed && !state.pending;
  $("btn-send").dataset.mode = stopMode ? "stop" : "send";
  $("btn-send").title = stopMode ? "Stop the agent" : "Send (Enter)";
  $("btn-send").setAttribute("aria-label", stopMode ? "Stop the agent" : "Send");
  $("btn-send").disabled = stopMode ? false : (!state.session || !state.agentConnected || state.pending || !typed);
  $("btn-stop").hidden = !stopMode;
  $("btn-export").disabled = !state.session || !state.agentConnected;
  $("btn-pause").disabled = !state.running || !state.agentConnected;
  $("btn-takeover").disabled = !state.running || !state.agentConnected;
  $("btn-new-task").disabled = state.running || !state.session;
  for (const id of ['engine-select', 'btn-use-saved', 'btn-restore']) $(id).disabled = state.running || state.projectBusy || !state.session?.project_id;
  if (!state.project?.head || $('revision-select').value === state.project.head) $('btn-restore').disabled = true;
  $("btn-pause").textContent = state.paused ? "Resume" : "Pause";
  $("btn-stop").classList.toggle("hidden", !state.running);
  $("run-progress").textContent = state.nativeOperation ? (state.running ? `Build attempt ${state.nativeAttempt || 1}/3${state.nativeParent ? ` · base ${state.nativeParent}` : ''}` : `${state.completed} operation(s) completed this run`) :
    state.researchCalls && !state.completed ? `${state.researchCalls} web lookup(s) · no CAD changes yet` : `${state.completed} / ${state.maxSteps} operations this run`;
  $("run-progress").title = "This run only; earlier saved revisions remain in the Model panel. Research and retry attempts are not completed CAD operations or a percentage of the task.";
  $("run-fill").style.width = `${Math.min(100, state.completed / state.maxSteps * 100)}%`;
  $("view-hint").textContent = state.running ? "Agent has control · Esc to stop" : state.session ? "Click the canvas to interact · Your keyboard controls the CAD application" : "A shared canvas for you and your agent";
  if (state.paused && state.running) $("composer-hint").textContent = "Send guidance to continue, or Take control to inspect and edit the CAD document";
  else if (state.phase === "awaiting" && state.running) $("composer-hint").textContent = "Send the next objective, or switch to Auto";
  else if (state.running) $("composer-hint").textContent = "Send changes at any time · Your guidance updates the plan";
  else if (state.canContinue && !state.freshTask) $("composer-hint").textContent = "Continue or refine this part · New task starts a separate goal";
  workspaceEditor.sync();
}
function connectionStatus() {
  chatPanel.connection(state.agentConnected);
  const el = $("connection-status");
  const connected = state.agentConnected && state.viewConnected;
  el.textContent = !state.session ? "Ready to start" : connected ? "Live" : "Reconnecting…";
  el.className = `connection-status ${state.session ? connected ? "live" : "lost" : ""}`;
  syncControls();
}
function resetChat() {
  chatPanel.reset();
  $("chat").innerHTML = emptyChat;
  state.lastEvent = 0;
  state.webSources = {}; state.researchCalls = 0; state.nativeOperation = false; state.nativeAttempt = 0;
  bindSuggestions();
}
function bindSuggestions() {
  document.querySelectorAll("[data-prompt]").forEach(button => button.onclick = () => {
    $("composer-input").value = button.dataset.prompt;
    $("composer-input").dispatchEvent(new Event("input"));
    $("composer-input").focus();
    if (!state.session) toast("Choose a CAD application, then send your task.");
  });
}

/* ---------------- helpers ---------------- */
function toast(text, kind = "") {
  if (state.replaying) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}
function safeSourceLink(source, label) {
  try {
    const url = new URL(source.url);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return document.createTextNode(label);
    const a = document.createElement('a'); a.href = url.href; a.textContent = label;
    a.target = '_blank'; a.rel = 'noopener noreferrer'; a.title = source.title || url.hostname; return a;
  } catch { return document.createTextNode(label); }
}
function showSources(sources, notes, container) {
  for (const source of sources || []) {
    state.webSources[source.id] = source;
    const row = document.createElement('div'); row.className = 'research-source';
    row.append(safeSourceLink(source, `${source.kind === 'search_result' ? 'Search result' : 'Read source'} · ${source.title}`));
    if (source.excerpt) { const p = document.createElement('p'); p.textContent = source.excerpt; row.append(p); }
    container.append(row);
  }
  for (const fact of notes?.facts || []) {
    const row = document.createElement('p'); row.textContent = `Sourced: ${fact.statement} `;
    if (state.webSources[fact.source_id]) row.append(safeSourceLink(state.webSources[fact.source_id], 'source'));
    row.title = fact.quote; container.append(row);
  }
  for (const [field, label] of [['assumptions', 'Assumption'], ['unknowns', 'Not established']]) for (const text of notes?.[field] || []) {
    const row = document.createElement('p'); row.textContent = `${label}: ${text}`; container.append(row);
  }
}

/* ---------------- status + launcher ---------------- */
function renderEndpointStatus() {
  const native = state.health.supervision?.native_operations || state.health.chat?.runtime?.startsWith('pi-coding-agent');
  const needsActionModel = !native || state.session?.engine === 'visual';
  $('pill-policy').classList.toggle('hidden', !needsActionModel);
  for (const name of ['policy', 'planner']) {
    const pill = $(`pill-${name}`), ready = state.health[name]?.ok;
    pill.classList.toggle('ok', ready === true);
    pill.classList.toggle('err', ready === false);
    const label = name === 'policy' ? 'Action model' : 'Assistant';
    pill.title = `${label}: ${ready === true ? 'last availability check passed' : ready === false ? 'last availability check failed; you can still try sending a message' : 'availability has not been confirmed; you can still send a message'}`;
  }
}
async function refreshStatus() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error('Status check unavailable');
    const status = await response.json();
    state.health = status; renderModelLabels(); if (!state.contextUsage) renderContextUsage(null);
    state.sessions = status.sessions;
    renderTabs();
    if (!state.restored) {
      state.restored = true;
      const saved = state.sessions.find(s => s.id === localStorage.getItem("cadpilot-session"));
      if (saved) attach(saved);
    }
    if (state.session) {
      const current = state.sessions.find(s => s.id === state.session.id);
      if (current) {
        Object.assign(state.session, current);
        $('engine-select').value = current.engine || 'visual';
        $('model-warning').textContent = current.manual_changes || current.agent_changes ? 'Viewport activity detected. FreeCAD document content is checked before native edits; camera and selection changes do not require a reset. Actual GUI model edits cannot be merged into the saved recipe.' : 'Edit dimensions through chat. Native tools build a new revision without closing your other documents.';
      }
      if (!current) { detachUI(); toast("This session has ended."); }
      else if (!current.app_alive) { $("connection-status").textContent = "Application closed"; $("connection-status").className = "connection-status lost"; }
    }
    renderEndpointStatus();
    const note = $("tool-note");
    if (status.missing_tools.length) {
      note.classList.remove("hidden");
      note.textContent = "A companion application window also opens on your desktop.";
    }
  } catch {
    for (const name of ['policy', 'planner']) state.health[name] = {...state.health[name], ok: null};
    renderEndpointStatus();
  }
}

async function loadApps() {
  let apps;
  try { ({ apps } = await (await fetch("/api/apps")).json()); }
  catch { $("app-grid").textContent = "Cannot reach the workspace. Refresh to reconnect."; return; }
  const grid = $("app-grid");
  grid.innerHTML = "";
  if (!Array.isArray(apps)) return;
  if (!apps.length) grid.innerHTML = '<p class="tool-note">No CAD applications detected.</p>';
  const shown = new Set();
  for (const app of apps) {
    const name = app.name.replace(" (AppImage)", "");
    if (shown.has(name.toLowerCase())) continue;
    shown.add(name.toLowerCase());
    const tile = document.createElement("button");
    tile.className = "app-tile";
    tile.innerHTML = `<div class="app-glyph">${escapeHTML(app.name[0])}</div>
      <div class="app-copy"><div class="t1">${escapeHTML(app.name.replace(" (AppImage)", ""))}</div><div class="t2">${app.name.toLowerCase().includes("openscad") ? "Code-based modeling" : "Parametric modeling"}</div></div>`;
    tile.querySelector('.app-glyph').replaceChildren(icon(app.name.toLowerCase().includes('openscad') ? 'code' : 'cad'));
    tile.onclick = () => launch(app);
    grid.appendChild(tile);
  }
  loadProjects();
}

async function loadProjects() {
  try {
    const {projects} = await (await fetch('/api/projects')).json();
    const list = $('recent-projects'); list.replaceChildren();
    if (projects.length) { const title = document.createElement('span'); title.textContent = 'Pick up where you left off'; list.append(title); }
    for (const p of projects.slice(0, 4)) {
      const button = document.createElement('button');
      const title = document.createElement('span'); title.className = 'project-title'; title.textContent = p.name;
      const revision = document.createElement('span'); revision.className = 'project-revision'; revision.textContent = `${p.head || 'New'} ↗`;
      button.append(icon('cad'), title, revision);
      button.title = `Open ${p.name}${p.head ? ' · '+p.head : ''}`;
      button.onclick = () => {
        const existing = state.sessions.find(s => s.project_id === p.id);
        if (existing) attach(existing); else launch({id:p.app_id, name:p.name}, p.id);
      }; list.append(button);
    }
  } catch { /* An older server may not support persistent projects yet. */ }
}

async function loadProject() {
  const session = state.session;
  $('model-panel').classList.toggle('hidden', !session?.project_id);
  if (!session?.project_id) { state.project = null; return; }
  try {
    const response = await fetch(`/api/projects/${session.project_id}`);
    if (!response.ok) throw new Error((await response.json()).detail);
    const project = await response.json();
    if (state.session?.id === session.id) renderProject(project);
  } catch (error) { toast(error.message, 'err'); }
}

function renderProject(project) {
  if (state.session?.project_id !== project.id) return;
  state.project = project;
  workspaceEditor.projectChanged(project);
  $('model-panel').classList.remove('hidden');
  $('model-name').textContent = project.head ? `${project.name} · ${project.head}` : 'No saved revision yet';
  $('model-status').textContent = project.geometry?.valid_solid ? '✓ Valid solid' : project.geometry?.valid_geometry ? `✓ ${project.geometry.solid_count} valid parts` : 'Native tools ready';
  $('model-status').dataset.state = project.geometry?.valid_solid || project.geometry?.valid_geometry ? 'valid' : 'empty';
  const g = project.geometry;
  $('model-measurements').textContent = g ? `${g.bounds_mm.map(v=>Number(v.toFixed(3))).join(' × ')} mm · ${g.volume_mm3.toFixed(2)} mm³ · ${g.representation || `${g.cuts?.length||0} material-removing cuts`}` : 'Describe a part to create an editable model and exports.';
  const views = $('model-views'); if (views) { views.replaceChildren();
    if (project.head && project.geometry?.views?.length) { for (const name of ['iso', 'top', 'front']) { if (!project.geometry.views.includes(name)) continue;
      const img = document.createElement('img'); img.src = `/api/projects/${project.id}/${project.head}/view-${name}.png?v=${project.head}`; img.alt = `${name} view of ${project.head}`; img.loading = 'lazy'; views.append(img); } } }
  $('model-parameters').replaceChildren();
  for (const p of project.design?.parameters || []) {
    const item = document.createElement('span'); item.textContent = `${p.name}: ${p.value}`; $('model-parameters').append(item);
  }
  $('model-downloads').replaceChildren();
  const artifacts=project.revisions.find(r=>r.id===project.head)?.sha256||{};
  for (const [name, label] of project.head ? [['model.FCStd','FreeCAD'],['model.scad','OpenSCAD'],['model.step','STEP'],['model.stl','STL'],['design.json','Recipe'],...Object.entries({'source.zip':'Source files','model.py':'Python','design-spec.json':'Requirements','parts.zip':'Part STLs'}).filter(([name])=>name in artifacts)] : []) {
    const link = document.createElement('a'); link.textContent = `↓ ${name==='design.json'&&project.design?.format==='source-v1'?'Build info':name==='model.scad'&&project.design?.language==='freecad-python'?'OpenSCAD mesh preview':label}`;
    link.href = `/api/projects/${project.id}/${project.head}/${name}`; link.download = ''; $('model-downloads').append(link);
  }
  $('model-research').classList.toggle('hidden', !project.research);
  $('model-research-content').replaceChildren();
  if (project.research) {
    showSources(project.research.sources, project.research.notes, $('model-research-content'));
    const link = document.createElement('a'); link.textContent = '↓ Research'; link.download = '';
    link.href = `/api/projects/${project.id}/${project.head}/research.json`; $('model-downloads').append(link);
  }
  $('revision-select').replaceChildren();
  for (const r of [...project.revisions].reverse()) {
    const option = document.createElement('option'); option.value = r.id; option.textContent = `${r.id} · ${r.name}${r.restored_from ? ` (from ${r.restored_from})` : ''}`; $('revision-select').append(option);
  }
  syncControls();
}

const workspaceEditor = new WorkspaceEditor({getState:()=>state,onProject:renderProject,toast});

async function projectOperation(body) {
  if (!state.session || state.projectBusy) return;
  const session = state.session; state.projectBusy = true; syncControls();
  try {
    const response = await fetch(`/api/sessions/${session.id}/project`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!response.ok) throw new Error((await response.json()).detail);
    const project = await response.json();
    if (state.session?.id === session.id) renderProject(project);
    await refreshStatus();
    if (project.warning) { toast(project.warning, 'err'); return; }
    toast(body.operation === 'restore' ? 'Restored as a new revision. Earlier work is preserved.' : body.operation === 'use_saved' ? 'Next native edit will use the saved recipe. Open CAD documents are untouched.' : 'Modeling tools updated.');
  } catch (error) { toast(error.message, 'err'); await loadProject(); }
  finally { state.projectBusy = false; syncControls(); }
}
$('engine-select').onchange = e => projectOperation({operation:'engine', engine:e.target.value});
$('btn-use-saved').onclick = () => projectOperation({operation:'use_saved'});
$('btn-restore').onclick = () => projectOperation({operation:'restore', revision:$('revision-select').value, expected_head:state.project?.head});
$('revision-select').onchange = syncControls;

function renderTabs() {
  const tabs = $("session-tabs");
  tabs.innerHTML = "";
  for (const s of state.sessions) {
    const tab = document.createElement("button");
    tab.className = "session-tab" + (state.session?.id === s.id ? " on" : "");
    tab.innerHTML = `<span class="tab-dot"></span><span class="tab-label">${escapeHTML(s.app.name.replace(" (AppImage)", ""))}</span><span class="tab-x" title="End session">×</span>`;
    tab.dataset.alive = String(!!s.app_alive);
    tab.querySelector(".tab-x").onclick = (e) => { e.stopPropagation(); endSession(s.id); };
    tab.onclick = () => attach(s);
    tabs.appendChild(tab);
  }
  const active = tabs.querySelector('.on');
  if (active && (active.offsetLeft < tabs.scrollLeft || active.offsetLeft + active.offsetWidth > tabs.scrollLeft + tabs.clientWidth)) tabs.scrollLeft = active.offsetLeft;
}

async function launch(app, projectId = null) {
  if (state.launching) return;
  state.launching = true;
  $("launcher").classList.add("hidden");
  $("booting").classList.remove("hidden");
  $("booting-text").textContent = `Starting ${app.name}…`;
  try {
    const res = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ app_id: app.id, project_id: projectId }) });
    if (!res.ok) throw new Error((await res.json()).detail || "launch failed");
    const session = await res.json(); attach(session);
    if (session.warning) toast(session.warning, 'err');
    refreshStatus();
  } catch (err) {
    $("booting").classList.add("hidden");
    $("launcher").classList.remove("hidden");
    toast(String(err.message || err), "err");
  } finally { state.launching = false; }
}

async function endSession(id) {
  const dialog = $("close-dialog");
  if (dialog.open) return;
  dialog.returnValue = "cancel";
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "close") return;
    try {
      const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Could not close the session. Please retry.");
      if (state.session?.id === id) detachUI();
      refreshStatus();
    } catch (error) { toast(error.message, "err"); }
  }, { once: true });
}

function detachUI() {
  state.project = null; $('model-panel').classList.add('hidden'); loadProjects();
  state.generation++;
  state.session = null;
  renderEndpointStatus();
  localStorage.removeItem("cadpilot-session");
  state.viewWS?.close(); state.agentWS?.close();
  state.agentConnected = state.viewConnected = state.running = state.pending = false;
  state.canContinue = state.freshTask = false;
  state.latestFrame = null;
  resetChat(); $("run-card").classList.add("hidden");
  $("screen").classList.add("hidden");
  $("launcher").classList.remove("hidden");
  $("booting").classList.add("hidden");
  $("vp-title").textContent = "Your workspace";
  $("vp-meta").textContent = "";
  for (const id of ["btn-shot", "btn-full", "btn-close-session"]) $(id).classList.add("hidden");
  setLocked(false);
  connectionStatus(); renderTabs();
}

/* ---------------- websockets with reconnect ---------------- */
function connectWS(path, handlers) {
  let closedByUs = false, attempts = 0, timer = null, current = null;
  const open = () => {
    if (closedByUs) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`);
    current = ws;
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => { if (!closedByUs) handlers.message(event); };
    ws.onopen = () => { if (closedByUs) { ws.close(); return; } attempts = 0; handlers.open?.(ws); };
    ws.onclose = (event) => {
      if (event.code === 4410) { location.replace('/onboarding'); return; }
      if (event.code === 4401) { location.replace('/login'); return; }
      if (closedByUs || !state.session) return;
      handlers.disconnected?.();
      if (event.code === 4404) { detachUI(); toast("This session is no longer available.", "err"); return; }
      timer = setTimeout(open, Math.min(8000, 400 * 2 ** Math.min(attempts++, 5)));
    };
    handlers.assign(ws);
  };
  open();
  return { close() { closedByUs = true; clearTimeout(timer); current?.close(); } };
}

/* ---------------- viewport ---------------- */
function attach(session) {
  if (state.session?.id === session.id) return;
  state.viewWS?.close?.(); state.agentWS?.close?.();
  const generation = ++state.generation;
  state.session = session;
  renderEndpointStatus();
  state.project = null; loadProject(); $('engine-select').value = session.engine || 'visual';
  state.freshTask = state.canContinue = false;
  state.pendingText = "";
  localStorage.setItem("cadpilot-session", session.id);
  state.agentConnected = state.viewConnected = state.pending = false;
  state.running = !!session.agent_active;
  state.latestFrame = null; state.decoding = false;
  state.startedAt = null; state.completed = 0;
  resetChat(); $("run-card").classList.add("hidden");
  setLocked(state.running); connectionStatus();
  $("screen").classList.add("hidden");
  state.firstFrame = false;
  $("vp-title").textContent = session.app.name.replace(" (AppImage)", "");
  $("vp-meta").textContent = `${session.width} × ${session.height}`;
  $("launcher").classList.add("hidden");
  $("booting").classList.remove("hidden");
  $("booting-text").textContent = "Connecting…";
  for (const id of ["btn-shot", "btn-full", "btn-close-session"]) $(id).classList.remove("hidden");
  renderTabs();

  const canvas = $("screen"), ctx = canvas.getContext("2d");
  const render = async () => {
    if (generation !== state.generation) return;
    if (state.decoding || !state.latestFrame) return;
    state.decoding = true;
    const data = state.latestFrame; state.latestFrame = null;
    try {
      const bmp = await createImageBitmap(new Blob([data], { type: "image/jpeg" }));
      if (generation !== state.generation) { bmp.close(); return; }
      if (canvas.width !== bmp.width || canvas.height !== bmp.height) { canvas.width = bmp.width; canvas.height = bmp.height; }
      ctx.drawImage(bmp, 0, 0); bmp.close();
      $("frame-info").textContent = `${session.width} × ${session.height}`;
      if (!state.firstFrame) {
        state.firstFrame = true;
        $("booting").classList.add("hidden");
        canvas.classList.remove("hidden");
        $("focus-hint").classList.toggle("hidden", state.locked);
      }
    } catch { /* A dropped JPEG is replaced by the next frame. */ }
    finally { if (generation === state.generation) { state.decoding = false; if (state.latestFrame) render(); } }
  };
  let viewSocket = null;
  state.viewWS = connectWS(`/ws/view/${session.id}`, {
    assign: (ws) => (viewSocket = ws), current: () => viewSocket,
    open: () => { state.viewConnected = true; connectionStatus(); },
    disconnected: () => { state.viewConnected = false; connectionStatus(); },
    message: (event) => {
      if (typeof event.data === "string") {
        const message = JSON.parse(event.data);
        if (message.t === "input_error") { toast(message.message, "err"); return; }
        if (message.t === "app_closed") {
          $("connection-status").textContent = "Application closed";
          $("connection-status").className = "connection-status lost";
          toast("The CAD application closed. Your activity is available to export.");
        }
        return;
      }
      state.latestFrame = event.data; render();
    },
  });
  state.viewWS.send = (obj) => { if (viewSocket?.readyState === 1 && !state.locked) viewSocket.send(JSON.stringify(obj)); };

  let agentSocket = null;
  state.agentWS = connectWS(`/ws/agent/${session.id}`, {
    assign: (ws) => (agentSocket = ws), current: () => agentSocket,
    open: () => { /* Snapshot establishes authoritative state before enabling controls. */ },
    disconnected: () => { state.agentConnected = false; state.pending = false; connectionStatus(); },
    message: (event) => handleAgentEvent(JSON.parse(event.data)),
  });
  state.agentWS.send = (obj) => { if (agentSocket?.readyState !== 1) return false; agentSocket.send(JSON.stringify(obj)); return true; };
}

const canvas = $("screen");
const BTN = { 0: "left", 1: "middle", 2: "right" };
const modifiers = e => [e.ctrlKey && "ctrl", e.shiftKey && "shift", e.altKey && "alt", e.metaKey && "meta"].filter(Boolean);
function coords(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(state.session.width - 1, Math.round((event.clientX - rect.left) * (state.session.width / rect.width)))),
    y: Math.max(0, Math.min(state.session.height - 1, Math.round((event.clientY - rect.top) * (state.session.height / rect.height)))),
  };
}
let lastMove = 0;
canvas.addEventListener("pointerdown", (e) => { e.preventDefault(); canvas.focus(); canvas.setPointerCapture(e.pointerId); state.viewWS?.send({ t: "down", ...coords(e), button: BTN[e.button] ?? "left", modifiers: modifiers(e) }); });
canvas.addEventListener("pointerup", (e) => { e.preventDefault(); state.viewWS?.send({ t: "up", ...coords(e), button: BTN[e.button] ?? "left" }); });
canvas.addEventListener("pointermove", (e) => { const now = performance.now(); if (now - lastMove < 24) return; lastMove = now; state.viewWS?.send({ t: "move", ...coords(e) }); });
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("wheel", (e) => { e.preventDefault(); state.viewWS?.send({ t: "scroll", ...coords(e), delta_y: -Math.sign(e.deltaY), delta_x: Math.sign(e.deltaX), modifiers: modifiers(e) }); }, { passive: false });
canvas.addEventListener("pointercancel", () => state.viewWS?.send({t:"release"}));
canvas.addEventListener("lostpointercapture", () => state.viewWS?.send({t:"release"}));
window.addEventListener("blur", () => state.viewWS?.send({t:"release"}));
canvas.addEventListener("paste", e => { if (state.locked) return; e.preventDefault(); const text = e.clipboardData?.getData("text/plain"); if (text) state.viewWS?.send({t:"text", text}); });
canvas.addEventListener("focus", () => $("focus-hint").classList.add("hidden"));
canvas.addEventListener("blur", () => state.session && state.firstFrame && !state.locked && $("focus-hint").classList.remove("hidden"));
const KEYMAP = { Enter: "Enter", Escape: "Escape", Backspace: "Backspace", Tab: "Tab", Delete: "Delete", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", Insert: "Insert", " ": "Space" };
canvas.addEventListener("keydown", (e) => {
  if (state.locked || !state.session) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return;
  e.preventDefault();
  const mods = [e.ctrlKey && "ctrl", e.shiftKey && "shift", e.altKey && "alt", e.metaKey && "meta"].filter(Boolean);
  // Keep keystrokes: Space/letters are CAD commands outside text fields. Only an actual
  // browser paste uses the UTF-8 clipboard transport.
  if (e.key.length === 1) state.viewWS?.send({ t: "key", key: e.key.toLowerCase(), modifiers: mods });
  else if (KEYMAP[e.key] || /^F\d{1,2}$/.test(e.key)) state.viewWS?.send({ t: "key", key: KEYMAP[e.key] || e.key, modifiers: mods });
});

$("btn-shot").onclick = () => {
  const a = document.createElement("a");
  a.download = `cadpilot-${Date.now()}.png`;
  a.href = canvas.toDataURL("image/png");
  a.click();
};
$("btn-full").onclick = () => ($("viewport").requestFullscreen ? $("viewport").requestFullscreen() : null);
$("btn-close-session").onclick = () => state.session && endSession(state.session.id);
$("btn-new-session").onclick = () => { detachUI(); };

/* ---------------- agent ---------------- */
function setLocked(locked) {
  state.locked = locked;
  $("viewport").classList.toggle("agent-live", locked);
  $("vp-badge").classList.toggle("hidden", !locked);
  if (locked) $("focus-hint").classList.add("hidden");
  $("btn-stop").classList.toggle("hidden", !locked);
  $("composer-hint").textContent = locked
    ? (state.mode === "manual" ? "Agent is executing — send the next objective when it's done" : "Agent is planning and executing autonomously")
    : "Enter to send · Shift-Enter for a new line";
  if (!locked) $("agent-cursor").classList.add("hidden");
  syncControls();
}

function overlayPos(px) {
  const rect = canvas.getBoundingClientRect(), vp = $("viewport").getBoundingClientRect();
  return { x: rect.left - vp.left + (px.x / state.session.width) * rect.width, y: rect.top - vp.top + (px.y / state.session.height) * rect.height };
}
function chipLabel(action) {
  return { click: `click · ${action.button} · ${action.x},${action.y}`, drag: `drag → ${action.end_x},${action.end_y}`,
    scroll: `scroll ${action.delta_y > 0 ? "up" : "down"}`, type_text: `type “${action.text}”`,
    key: `key ${[...(action.modifiers || []), action.key].join("+")}` }[action.type] || action.type;
}
function showAgentAction(msg) {
  if (state.replaying) return;
  const generation = state.generation;
  const { action, px } = msg;
  if (px.x !== undefined && state.session) {
    const p = overlayPos(px);
    const cursor = $("agent-cursor");
    cursor.classList.remove("hidden");
    cursor.style.left = p.x + "px"; cursor.style.top = p.y + "px";
    setTimeout(() => {
      if (generation !== state.generation || !state.session) return;
      if (action.type === "click") {
        const r = document.createElement("div");
        r.className = `ripple ${action.button}`;
        r.style.left = p.x + "px"; r.style.top = p.y + "px";
        $("viewport").appendChild(r); setTimeout(() => r.remove(), 650);
      } else if (action.type === "drag") {
        const q = overlayPos({ x: px.end_x, y: px.end_y });
        const line = document.createElement("div");
        line.className = "drag-line";
        line.style.left = p.x + "px"; line.style.top = p.y + "px";
        line.style.width = Math.hypot(q.x - p.x, q.y - p.y) + "px";
        line.style.transform = `rotate(${Math.atan2(q.y - p.y, q.x - p.x)}rad)`;
        $("viewport").appendChild(line); setTimeout(() => line.remove(), 1200);
        setTimeout(() => { cursor.style.left = q.x + "px"; cursor.style.top = q.y + "px"; }, 140);
      }
    }, 310);
  }
  const chip = document.createElement("div");
  chip.className = "ov-chip"; chip.textContent = chipLabel(action);
  $("overlay-chips").appendChild(chip); setTimeout(() => chip.remove(), 2500);
}
function chipDetail(a) {
  return { click: `${a.button} ${a.x},${a.y}`, drag: `${a.x},${a.y}→${a.end_x},${a.end_y}`,
    scroll: a.delta_y > 0 ? "up" : "down", type_text: `“${a.text}”`,
    key: [...(a.modifiers || []), a.key].join("+") }[a.type] || "";
}
function handleAgentEvent(msg) {
  if (msg.t === "snapshot") {
    // Reconcile by identity. Do not reset mounted turns, disclosures, selections,
    // or scroll position when a transport reconnects.
    chatPanel.replay(msg.transcript || msg.events || [], msg.active,msg.research_agents || []);
    state.replaying = true; state.lastEvent = 0;
    try { for (const event of msg.events || []) updateAgentControls(event); }
    finally { state.replaying = false; }
    state.running = msg.active; state.paused = msg.paused; state.mode = msg.mode;
    state.completed = msg.completed_steps; state.maxSteps = msg.max_steps;
    state.startedAt = msg.started_at; state.phase = msg.phase;
    state.canContinue = msg.can_continue ?? !!msg.task;
    state.agentConnected = true; state.pending = false;
    state.question = msg.pending_question || null;
    $('web-search-toggle').disabled = msg.chat_protocol !== 1;
    if (msg.context_usage) { state.contextUsage = msg.context_usage; renderContextUsage(msg.context_usage); }
    renderImageContext(msg.image_context);
    if (msg.persistence_warning) toast(msg.persistence_warning,'err');
    if (typeof msg.web_enabled === 'boolean') $('web-search-toggle').checked = msg.web_enabled;
    if (msg.task) { $("run-card").classList.remove("hidden"); $("run-task").textContent = msg.task; }
    setModeUI(msg.mode); setLocked(msg.active); connectionStatus();
    if (msg.active) updatePhase(msg.phase);
    loadProject();
    return;
  }
  chatPanel.consume(msg);
  updateAgentControls(msg);
}
function updateAgentControls(msg) {
  if (msg.id && msg.id <= state.lastEvent) return;
  if (msg.id) state.lastEvent = msg.id;
  switch (msg.t) {
    case "user": state.pending = false; state.pendingText = ""; break;
    case "guidance": state.pending = false; updatePhase("steering"); break;
    case "request_error":
      state.pending = false; chatPanel.reject();
      if (!input.value && state.pendingText) input.value = state.pendingText;
      state.pendingText = ""; toast(msg.message, "err"); break;
    case "mode": state.mode = msg.mode; setModeUI(msg.mode); break;
    case "web_setting": $('web-search-toggle').checked = msg.enabled; break;
    case "pause": state.paused = msg.paused; updatePhase(msg.paused ? "paused" : "planning"); break;
    case "context_usage": state.contextUsage = msg; renderContextUsage(msg); break;
    case "image_context": renderImageContext(msg); break;
    case "question": state.question = msg; state.pending = false; updatePhase("awaiting_answer"); break;
    case "answer": if (state.question?.question_id === msg.question_id) state.question = null; state.pending = false; updatePhase("planning"); break;
    case "phase": state.completed = msg.completed_steps; state.phase = msg.phase; if (msg.phase !== "idle") updatePhase(msg.phase); break;
    case "control":
      state.running = msg.locked; state.pending = false;
      if (msg.locked) {
        state.completed = 0; state.startedAt = msg.ts; state.endedAt = null;
        state.researchCalls = 0; state.nativeOperation = false; state.nativeAttempt = 0;
        state.canContinue = true; state.freshTask = false;
        state.mode = msg.mode; setModeUI(msg.mode); state.paused = false;
        $("run-card").classList.remove("hidden"); $("run-task").textContent = msg.task;
      } else { state.awaiting = false; state.endedAt = msg.ts; input.placeholder = "What would you like to make?"; }
      setLocked(msg.locked); break;
    case "await_intent":
      state.awaiting = true;
      input.placeholder = "What should we do next?";
      if (!state.replaying) input.focus(); break;
    case "planning": updatePhase("planning"); break;
    case "research_start": state.researchCalls = msg.call; updatePhase("researching"); break;
    case "research_result":
      for (const source of msg.sources || []) state.webSources[source.id] = source;
      break;
    case "native_attempt": state.nativeAttempt = msg.attempt; state.nativeParent = msg.parent; break;
    case "artifact": if (msg.project) { renderProject(msg.project); if (!state.replaying) $('model-panel').open = true; } break;
    case "intent": state.nativeOperation = msg.tool === "native_model"; state.awaiting = false; break;
    case "action": showAgentAction(msg); break;
    case "step_done": state.completed++; break;
    case "recovery": updatePhase("recovering"); break;
    case "done": {
      const turn = chatPanel.state.byId.get(msg.turn_id || chatPanel.state.current);
      updatePhase(['failed', 'interrupted'].includes(turn?.status) ? 'error' : msg.reason.includes("stopped") ? "stopped" : msg.reason.includes("budget") ? "budget" : "review"); break;
    }
    case "error": updatePhase("error"); toast(msg.message, "err"); break;
  }
  syncControls();
}

function updatePhase(phase) {
  const turn = chatPanel.state.byId.get(chatPanel.state.current);
  if (phase === 'awaiting' && ['failed', 'interrupted'].includes(turn?.status)) phase = 'error';
  if (phase === 'researching') { $('run-phase').textContent = 'Looking up sources and specifications'; return; }
  if (phase === 'awaiting_answer') { $('run-phase').textContent = 'Waiting for your answer'; return; }
  if (phase === 'modeling' || phase === 'building') { $('run-phase').textContent = phase === 'modeling' ? 'Designing the parametric model' : 'Checking geometry and saving exports'; return; }
  $("run-phase").textContent = ({ waiting_screen: "Waiting for CAD", planning: "Planning next step", steering: "Updating the plan", thinking: "Choosing actions", executing: "Working in CAD", verifying: "Checking the result", recovering: "Correcting the approach", awaiting: "Waiting for your objective", paused: "Paused · guidance welcome", pausing: "Pausing after this step…", stopped: "Stopped · you have control", review: "Result ready for your review", budget: "Action budget reached · send guidance to continue", error: "Needs attention" })[phase] || "Ready";
}
function setModeUI(mode) {
  $("mode-toggle").querySelectorAll("button").forEach(button => { button.classList.toggle("on", button.dataset.mode === mode); button.setAttribute("aria-pressed", String(button.dataset.mode === mode)); });
}

/* ---------------- composer ---------------- */
const input = $("composer-input");
function autosize() { input.style.height = "22px"; if (input.value) input.style.height = Math.min(160, input.scrollHeight) + "px"; input.style.overflowY = input.scrollHeight > 160 ? "auto" : "hidden"; }
input.addEventListener("input", () => { autosize(); syncControls(); });
autosize();
function send() {
  const text = input.value.trim();
  if (!text) return;
  if (!state.session) { toast("start a session first"); return; }
  if (state.pending) return;
  if (!state.agentConnected) { toast("Reconnecting. Your task is still here."); return; }
  // A cached /models probe is advisory. Let Pi make the actual request and
  // report provider errors; an unused action endpoint must never block chat.
  let sent;
  const attachments = (state.attachments || []).filter(a => a.kind === 'image').map(a => a.id);
  if (!state.running) {
    sent = state.agentWS.send({ t: "start", task: text, mode: state.mode, new_task: state.freshTask, attachments });
  } else sent = state.agentWS.send({ t: "intent", text, attachments });
  if (sent) { state.attachments = []; renderAttachments(); }
  if (!sent) { toast("Connection interrupted. Please send again.", "err"); return; }
  chatPanel.optimistic(text);
  state.pending = true; state.pendingText = text; input.value = ""; autosize(); syncControls();
}
function renderAttachments() {
  const box = $("attachment-chips"); box.replaceChildren();
  for (const a of state.attachments || []) {
    const chip = document.createElement('span'); chip.className = 'attachment-chip';
    chip.textContent = (a.kind === 'image' ? '🖼 ' : '⬡ ') + a.label;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', 'Remove ' + a.label);
    remove.onclick = () => { state.attachments = state.attachments.filter(x => x !== a); renderAttachments(); };
    chip.append(remove); box.append(chip);
  }
  box.hidden = !(state.attachments || []).length;
}
/* ---------------- popover menus ---------------- */
const menus = [];
function popover(buttonId, menuId, render) {
  const button = $(buttonId), menu = $(menuId);
  const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  const open = () => { for (const other of menus) other.close(); if (render) render(menu); menu.hidden = false; button.setAttribute('aria-expanded', 'true'); };
  button.onclick = (event) => { event.stopPropagation(); menu.hidden ? open() : close(); };
  menu.addEventListener('click', event => event.stopPropagation());
  const entry = { button, menu, close, open }; menus.push(entry); return entry;
}
document.addEventListener('click', () => { for (const m of menus) m.close(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') for (const m of menus) m.close(); });
const attachMenu = popover('btn-attach', 'menu-attach');
$("menu-add-files").onclick = () => { attachMenu.close(); if (!state.session?.project_id) { toast("Open a FreeCAD/OpenSCAD session with a native project first"); return; } $("attach-input").click(); };
$("attach-folder-input").onchange = async () => { for (const file of $("attach-folder-input").files) await uploadAttachment(file); $("attach-folder-input").value = ''; };

/* ---------------- settings: providers, active model, effort, web search ---------------- */
state.settings = null;
async function loadSettings() {
  try { const response = await fetch('/api/settings'); if (response.ok) state.settings = await response.json(); } catch {}
  renderModelLabels(); return state.settings;
}
async function saveSettings(patch) {
  const base = state.settings || await loadSettings(); if (!base) return null;
  const body = { ...base, ...patch, models: (patch.models || base.models).map(m => ({ ...m, api_key: m.api_key ?? (m.has_key ? '••••••••' : '') })) };
  const response = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Settings were not saved');
  state.settings = data; renderModelLabels(); refreshStatus(); return data;
}
function menuItem(label, note, checked, onclick) {
  const item = document.createElement('button'); item.type = 'button'; item.className = 'popover-item'; item.setAttribute('role', 'menuitemradio'); item.setAttribute('aria-checked', String(!!checked));
  item.append(document.createTextNode(label)); if (note) { const n = document.createElement('span'); n.className = 'popover-note'; n.textContent = note; item.append(n); }
  item.onclick = onclick; return item;
}
const modelMenu = popover('model-name-label', 'menu-model', menu => {
  menu.replaceChildren();
  const settings = state.settings;
  if (!settings) { menu.append(menuItem('Loading…', '', false, () => {})); loadSettings().then(() => modelMenu.open()); return; }
  for (const m of settings.models) menu.append(menuItem(m.name, `${m.model} · ${m.base_url}`, m.name === settings.active_model, async () => { modelMenu.close(); try { await saveSettings({ active_model: m.name }); toast(`Model: ${m.name}`); } catch (e) { toast(e.message, 'err'); } }));
  menu.append(menuItem('Manage providers…', '', false, () => { modelMenu.close(); openSettings(); }));
});
const effortMenu = popover('model-effort-label', 'menu-effort', menu => {
  menu.replaceChildren();
  for (const effort of (state.settings?.efforts || ['off', 'low', 'medium', 'high', 'xhigh'])) menu.append(menuItem(effort.charAt(0).toUpperCase() + effort.slice(1), '', effort === state.settings?.reasoning_effort, async () => { effortMenu.close(); try { await saveSettings({ reasoning_effort: effort }); } catch (e) { toast(e.message, 'err'); } }));
});
function modelRow(model, active, index) {
  const row = document.createElement('div'); row.className = 'settings-model'; row.dataset.index = index;
  const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'active-model'; radio.checked = active; radio.title = 'Active model';
  const name = document.createElement('input'); name.placeholder = 'Name (e.g. local qwen)'; name.value = model.name || ''; name.dataset.field = 'name';
  const modelId = document.createElement('input'); modelId.placeholder = 'Model id (e.g. qwen3.8-27b)'; modelId.value = model.model || ''; modelId.dataset.field = 'model';
  const url = document.createElement('input'); url.placeholder = 'Endpoint base URL (http://host:8000/v1)'; url.value = model.base_url || ''; url.dataset.field = 'base_url';
  const key = document.createElement('input'); key.type = 'password'; key.placeholder = model.has_key ? 'API key stored (leave to keep)' : 'API key (optional)'; key.value = model.has_key ? '••••••••' : ''; key.dataset.field = 'api_key';
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove'; remove.textContent = 'remove'; remove.onclick = () => row.remove();
  const line2 = document.createElement('div'); line2.className = 'span-2'; line2.append(url);
  const line3 = document.createElement('div'); line3.className = 'span-2'; line3.append(key, remove);
  const limits = document.createElement('div'); limits.className = 'span-2';
  for (const [field, label] of [['context_window', 'Context window (tokens, auto if empty)'], ['thinking_token_budget', 'Thinking budget (tokens, optional)'], ['max_images_per_request', 'Images per request (endpoint limit; default 16)']]) {
    const input = document.createElement('input'); input.type = 'number'; input.min = field === 'context_window' ? '4096' : '1';
    input.placeholder = label; input.title = label; input.setAttribute('aria-label', label); input.dataset.field = field; input.value = model[field] || ''; limits.append(input);
  }
  modelId.addEventListener('change', () => { if (modelId.value !== model.model) for (const input of limits.querySelectorAll('input')) input.value = ''; });
  const spacer = document.createElement('span'); const spacer2 = document.createElement('span');
  row.append(radio, name, modelId, spacer, line2, spacer2, line3, document.createElement('span'), limits);
  return row;
}
async function openSettings() {
  const settings = await loadSettings(); if (!settings) { toast('Settings are unavailable', 'err'); return; }
  $('settings-web').checked = settings.web_search;
  const effort = $('settings-effort'); effort.replaceChildren(); for (const e of settings.efforts) { const o = document.createElement('option'); o.value = e; o.textContent = e; effort.append(o); } effort.value = settings.reasoning_effort;
  const list = $('settings-models'); list.replaceChildren(); settings.models.forEach((m, i) => list.append(modelRow(m, m.name === settings.active_model, i)));
  $('settings-status').textContent = ''; $('settings-dialog').showModal();
}
$('btn-settings').onclick = openSettings;
$('settings-close').onclick = () => $('settings-dialog').close();
$('settings-add-model').onclick = () => $('settings-models').append(modelRow({ name: '', model: '', base_url: 'http://127.0.0.1:8000/v1' }, !$('settings-models').children.length, $('settings-models').children.length));
$('settings-save').onclick = async () => {
  const rows = [...$('settings-models').querySelectorAll('.settings-model')];
  const models = rows.map(row => { const m = {}; for (const input of row.querySelectorAll('input[data-field]')) m[input.dataset.field] = input.type === 'number' ? (input.value ? Number(input.value) : null) : input.value; return m; });
  const activeRow = rows.find(row => row.querySelector('input[type=radio]').checked);
  const active_model = activeRow ? activeRow.querySelector('input[data-field=name]').value : (models[0]?.name || '');
  try {
    const saved = await saveSettings({ web_search: $('settings-web').checked, reasoning_effort: $('settings-effort').value, models, active_model });
    $('settings-status').textContent = 'Saved'; $('web-search-toggle').checked = saved.web_search; setTimeout(() => $('settings-dialog').close(), 400);
  } catch (error) { $('settings-status').textContent = error.message; }
};
loadSettings();
$("attach-input").onchange = async () => {
  for (const file of $("attach-input").files) await uploadAttachment(file);
  $("attach-input").value = '';
};
async function uploadAttachment(file) {
  if (!state.session?.project_id) { toast("Open a FreeCAD/OpenSCAD session with a native project first"); return; }
  const form = new FormData(); form.append('file', file, file.name || 'pasted-image.png');
  try {
    const response = await fetch(`/api/sessions/${state.session.id}/attachments`, { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Upload failed');
    state.attachments = state.attachments || [];
    if (data.kind === 'reference') { toast(`Reference model ${data.name} added to the project`); state.attachments.push({ kind: 'reference', label: data.name }); }
    else state.attachments.push({ kind: 'image', id: data.id, label: file.name || 'pasted image' });
  } catch (error) { toast(error.message, 'err'); }
  renderAttachments();
}
// Paste an image straight into the chat: every image item on the clipboard becomes an attachment.
input.addEventListener('paste', async (event) => {
  const items = [...(event.clipboardData?.items || [])].filter(item => item.kind === 'file' && item.type.startsWith('image/'));
  if (!items.length) return;
  event.preventDefault();
  for (const item of items) { const file = item.getAsFile(); if (file) await uploadAttachment(file); }
  input.focus();
});
document.addEventListener('dragover', event => { if (event.dataTransfer?.types?.includes('Files')) event.preventDefault(); });
document.addEventListener('drop', async (event) => {
  const files = [...(event.dataTransfer?.files || [])]; if (!files.length) return;
  event.preventDefault();
  for (const file of files) await uploadAttachment(file);
});
window.cadpilotAnswer = (questionId, selected, text) => {
  if (!state.agentWS?.send({ t: "answer", question_id: questionId, selected, text })) { toast("Connection interrupted. Please answer again.", "err"); return; }
  state.pending = true; syncControls();
};
$("btn-new-task").onclick = () => { state.freshTask = true; input.placeholder = "Describe a new goal. The CAD document stays open."; $("composer-hint").textContent = "New goal · Existing geometry is preserved"; input.focus(); };
$("btn-send").onclick = () => { if ($("btn-send").dataset.mode === "stop") state.agentWS?.send({ t: "stop" }); else send(); };
input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
$("btn-stop").onclick = () => state.agentWS?.send({ t: "stop" });  // hidden mirror of the stop mode, for automation
$("mode-toggle").querySelectorAll("button").forEach((b) => (b.onclick = () => {
  if (state.running && !state.agentWS?.send({ t: "mode", mode: b.dataset.mode })) { toast("Reconnect before changing modes"); return; }
  state.mode = b.dataset.mode;
  setModeUI(state.mode);
  if (!state.running) $("composer-hint").textContent = state.mode === "auto"
    ? "Auto — describe the whole task, the agent plans and executes each step"
    : "Manual — you give one objective per step";
}));

$("btn-pause").onclick = () => state.agentWS?.send({ t: state.paused ? "resume" : "pause" });
$("btn-takeover").onclick = () => state.agentWS?.send({ t: "stop" });
$("btn-help").onclick = () => $("help-dialog").showModal();
$("btn-export").onclick = async () => {
  if (!state.session) return;
  const id = state.session.id;
  try {
    const response = await fetch(`/api/sessions/${id}/activity`);
    if (!response.ok) throw new Error("Could not export activity");
    const data = await response.json();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `cadpilot-${id}-activity.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, "err"); }
};
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && state.running && !document.querySelector("dialog[open]")) { event.preventDefault(); state.agentWS?.send({ t: "stop" }); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); input.focus(); }
});
const resizer = $("panel-resize");
function resizePanel(width) {
  width = Math.max(320, Math.min(640, Math.min(window.innerWidth * .48, width)));
  document.documentElement.style.setProperty("--panel-width", `${width}px`);
  resizer.setAttribute("aria-valuenow", String(Math.round(width)));
  localStorage.setItem("cadpilot-panel-width", String(width));
}
if (localStorage.getItem("cadpilot-panel-width")) resizePanel(Number(localStorage.getItem("cadpilot-panel-width")) || 390);
resizer.addEventListener("pointerdown", event => { resizer.setPointerCapture(event.pointerId); event.preventDefault(); });
resizer.addEventListener("pointermove", event => { if (resizer.hasPointerCapture(event.pointerId)) resizePanel(event.clientX); });
resizer.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); resizePanel($("agent-panel").offsetWidth + (event.key === "ArrowRight" ? 20 : -20)); } });
setInterval(() => {
  if (state.startedAt) {
    const seconds = Math.max(0, Math.floor((state.running ? Date.now() / 1000 : state.endedAt || state.startedAt) - state.startedAt));
    $("run-time").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
}, 1000);

/* ---------------- chat preferences ---------------- */
$('agent-panel').addEventListener('keydown', event => {
  if(event.key !== 'Escape') return;
  const preview=event.target.closest('.streaming-answer')?.querySelector('.citation-preview:not([hidden])');
  if(preview){preview.querySelector('.source-close').click();event.stopPropagation();event.preventDefault();}
}, true);
$('btn-mobile-view').onclick = event => {
  const canvas = document.documentElement.dataset.mobileView !== 'canvas';
  document.documentElement.dataset.mobileView = canvas ? 'canvas' : 'chat';
  event.target.textContent = canvas ? 'Chat' : 'Canvas';
  event.target.setAttribute('aria-label', canvas ? 'Show conversation' : 'Show CAD canvas');
};
$('web-search-toggle').onchange = async event => {
  const enabled = event.target.checked;
  if (state.agentWS && !state.agentWS.send({t:'web_setting', enabled})) { event.target.checked = !enabled; toast('Reconnect to change web search.'); return; }
  try { await saveSettings({ web_search: enabled }); } catch (error) { toast(error.message, 'err'); }
};
// Appearance: one toggle in the header (explicit dark/light, persisted). No motion setting.
function applyAppearance(selected) {
  document.documentElement.dataset.theme = selected;
  document.documentElement.dataset.motion = 'system';
  $('btn-theme').title = $('btn-theme').ariaLabel = `Switch to ${selected === 'dark' ? 'light' : 'dark'} theme`;
  localStorage.setItem('cadpilot-theme', selected); localStorage.setItem('cadpilot-appearance-version', '2');
}
const savedTheme = localStorage.getItem('cadpilot-theme');
applyAppearance(['dark', 'light'].includes(savedTheme) ? savedTheme : 'dark');
$('btn-theme').onclick = () => applyAppearance(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

/* ---------------- composer toolbar: model, effort, context window ---------------- */
function renderImageContext(usage) {
  const notice = $('image-context-notice');
  notice.hidden = !usage?.reference_omitted;
  notice.textContent = usage?.reference_omitted ? `Viewing ${usage.reference_total - usage.reference_omitted} of ${usage.reference_total} reference images in this request. The assistant can open the rest in batches; all originals remain saved.` : '';
}
function renderContextUsage(usage) {
  const ring = $('context-ring'), arc = $('context-arc'), tip = $('context-tip'); if (!ring) return;
  const used = (usage?.prompt_tokens || 0) + (usage?.completion_tokens || 0);
  const max = usage?.max_context || state.health?.planner?.max_model_len || 0;
  const fraction = max ? Math.min(1, used / max) : 0;
  arc.setAttribute('stroke-dasharray', `${(fraction * 44).toFixed(2)} 44`);
  ring.dataset.level = fraction > 0.95 ? 'full' : fraction > 0.8 ? 'high' : 'ok';
  const pct = Math.round(fraction * 100);
  ring.setAttribute('aria-label', max ? `Context window ${pct}% used: ${used.toLocaleString()} of ${max.toLocaleString()} tokens` : 'Context window usage unknown');
  tip.innerHTML = '';
  const title = document.createElement('div'); title.textContent = max ? `Context window · ${pct}% used` : 'Context window';
  const bar = document.createElement('div'); bar.className = 'context-bar'; const fill = document.createElement('i'); fill.style.width = `${pct}%`; bar.append(fill);
  const detail = document.createElement('div');
  detail.textContent = max ? `${used.toLocaleString()} / ${max.toLocaleString()} tokens (last request: ${(usage?.prompt_tokens || 0).toLocaleString()} in, ${(usage?.completion_tokens || 0).toLocaleString()} out)` : 'No request measured yet';
  tip.append(title, bar, detail);
}
function renderModelLabels() {
  const settings = state.settings, planner = state.health?.planner || {};
  const active = settings?.models?.find(m => m.name === settings.active_model);
  $('model-name-label').textContent = active?.name || planner.model || 'model';
  $('model-name-label').title = active ? `${active.model} at ${active.base_url} · click to switch` : 'Switch model';
  const effort = settings?.reasoning_effort || planner.native_reasoning_effort || planner.reasoning_effort;
  $('model-effort-label').textContent = effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : '—';
}
{ const ring = $('context-ring'), tip = $('context-tip');
  const show = () => { tip.hidden = false; }, hide = () => { tip.hidden = true; };
  ring.addEventListener('mouseenter', show); ring.addEventListener('mouseleave', hide); ring.addEventListener('focus', show); ring.addEventListener('blur', hide);
  renderContextUsage(null); }

/* ---------------- boot ---------------- */
loadApps();
refreshStatus();
bindSuggestions(); syncControls();
setInterval(refreshStatus, 6000);
