# CADPilot — agent harness

A Lovable-style desktop harness for the CAD visual-action model: an agent chat panel on the left,
a live interactive CAD viewport on the right. Each session runs a CAD application on its own
virtual X display; the browser streams JPEG frames over a WebSocket onto a canvas and forwards
your mouse/keyboard via the X11 XTEST extension (pure `python-xlib` — no x11vnc/xdotool needed).
The visual policy uses the *same* display: screenshots in, strict-JSON grid actions out, in exactly the
`cad-trajectory-1.0` conversation layout the model is trained on (text log of earlier steps +
current screenshot).

The default FreeCAD/OpenSCAD chat runs on **Pi's `AgentSession` SDK**
(`@earendil-works/pi-coding-agent` 0.85.1), with Pi's file/shell tools and CADPilot's CAD tools.
Pi owns model requests, the agent/tool loop, conversation persistence, steering, retries and
compaction. CADPilot owns CAD execution, geometry validation, revisions, research and the UI.
Model-written Python runs in a networkless project sandbox; a separate trusted kernel
validates its exported shapes. The trained desktop policy is still available in
**Visual only** mode for evaluating GUI actions; it uses its original trajectory harness.
The **Model** panel exposes saved dimensions, measured geometry, downloads and revision history.
Choose **Visual only** there when evaluating or using the trained desktop policy alone.

```bash
harness/pi/setup.sh       # installs pinned Pi dependencies and a local Node if needed
harness/run.sh            # serves http://127.0.0.1:7800
```

Or build and run CAD, the harness and SearXNG in Docker with owner login:

```bash
harness/docker.sh up      # serves http://127.0.0.1:7801
harness/docker.sh connect # shell in the CAD container
```

The generated login password is in `harness/.docker/owner-password` (username `admin`).
See [Docker setup and persistence](docker/README.md). Model generation is unlimited
by default; Stop cancels it. Optional positive request timeouts have no hard ceiling.

## Pieces

| file | role |
|---|---|
| `server/detect.py` | finds CAD apps: PATH binaries, flatpaks, snaps, `.desktop` (Engineering/CAD), and portable AppImages under `harness/apps/*-extracted/` |
| `server/sessions.py` | per-session X server (Xvfb headless, Xephyr fallback) + app process lifecycle |
| `server/xscreen.py` | frame capture and XTEST input injection for one display |
| `pi/runtime.mjs`, `server/pi_agent.py` | upstream Pi session → CAD tool bridge → browser events |
| `server/agent.py`, `planning.py`, `supervision.py` | CAD executor, application controls and legacy visual-policy harness |
| `server/design.py`, `cad_worker.py`, `projects.py` | bounded parametric graph → sandboxed native compiler → validated, persistent revisions |
| `server/operations.py` | persistent typed operation ledger (contract v2: anchored primitives, face holes, shell, brief), automatic graph wiring, targeted replay/edit |
| `server/grounding.py` | measurements presented as found must appear in a research fact or a user message |
| `server/research.py`, `browser_research.py`, `browser_proxy.py` | local Chromium search/page reading, bounded PDF extraction, public-only network access and cited specification notes |
| `server/presentation.py`, `freecad_observer.py`, `native.py` | open private working copies; read-only FreeCAD state observation and a separate fixed open-revision mailbox |
| `server/clipboard.py` | session-local UTF-8 paste with previous text clipboard preservation |
| `server/protocol.py`, `security.py`, `processes.py`, `journal.py` | bounded input, same-origin controls, process cleanup and diagnostics |
| `server/app.py` | FastAPI: REST + `/ws/view/{sid}` (frames+input) + `/ws/agent/{sid}` (chat/events) |
| `web/` | the UI (no build step) |
| `config.yaml` | model endpoints, resolution, fps, step budget |

## Agent modes

* **Manual** — you guide each local objective; the planner translates conversational guidance
  into small operations. Partial operations continue until that objective is addressed.
* **Auto** — the planner works toward the full task and incorporates subsequent changes.
  Both modes require the planner and policy endpoints. Each plan includes an immediate expected
  result; waiting, asking a concrete question, and answering without editing are explicit decisions.

While the agent holds control the viewport is **locked**: user input is dropped, an animated
gradient border glows around the pane, a synthetic cursor glides to each target, clicks ripple
(color-coded by button), drags draw their path, and every action appears as a chip in the pane
and in the step log.

## Web research

`research.enabled: true` gives the planner a general read-only research action: a search query
or public URL, plus what to look for. It can look up products, interfaces, materials, standards
and CAD techniques while modeling. User-specified dimensions do not need a lookup.

Docker uses SearXNG's JSON API for web and image search (`CADPILOT_SEARXNG_URL`).
The local fallback is adapted from the supplied `lm-chat-proxy.js` / `LM-chatUI-public-v5.html` references:
local Chromium with DuckDuckGo → Bing → Brave fallback, DOM result extraction and readable
page text. **No paid search API, API key, public CORS relay, or Jina dependency.** Chromium
runs on the harness host, like the reference local proxy; this is not a pure in-tab CORS
implementation. The reference files are unchanged. No browser logins, personal profiles,
media downloader, or model-authored browser scripts are exposed.

Search engines can still throttle, challenge, fail or return poor results. Entirely off-topic
responses are rejected; failures go back to the planner. Successful reads are cached for
15 minutes within the conversation, identical recent failures are not hammered, and the
default budget is 10 research calls per run. This is a no-subscription implementation, not
a guarantee of unlimited third-party availability.

Search snippets are leads, not established dimensions. The planner reads primary sources;
fact notes require a real retrieved source ID and an exact supporting quote. Assumptions and
unknowns are separate. The app shows source links and notes, and saves a hashed `research.json`
with each researched revision. Restoring that revision also restores its evidence. These
are model-extracted claims with provenance, **not certification of interpretation, fit or airflow**.

HTML runs in a fresh sandboxed Chromium context. A private authenticated proxy validates DNS
and connects to the checked public IP; private/loopback/metadata destinations, nonstandard
ports and non-HTTP(S) URLs are blocked. Cookies are not shared with CAD or personal browsers.
WebSockets, service workers, downloads, frames and non-GET page requests are disabled.
Network bytes, connections, research context and tool duration are bounded. Source text is
untrusted data, never part of system instructions. Page extraction removes consent dialogs
without dropping the underlying product page that a dialog marks `aria-hidden`.

Direct `.pdf` URLs use a separate networkless, resource-limited `pdftotext` worker: 12 MiB
download limit, first 40 pages, bounded excerpts, no OCR. Scanned drawings, arbitrary CAD
imports and archive extraction are not supported. A blocked or unsupported source is reported
as such; the agent must not invent the missing dimension.

## Serving the policy

```bash
# after training: merged weights or LoRA on the base
vllm serve runs/cad-policy-v1-64k/merged --served-model-name cad-policy --port 8001 \
  --default-chat-template-kwargs '{"enable_thinking": false}'
```

`config.yaml` currently uses the general model on `:8000` for both roles. The trained policy
will use `:8001` once its full run completes and it is served.

Policy requests use an explicit shared action prompt and JSON-Schema constrained generation
(`policy.structured_output: true`). Invalid replies receive the exact validation error and up
to two correction attempts for the same screenshot/intent. Truncated output is never repaired
or executed. Failed replies are retained in exported activity for diagnosis. After three
invalid replies the step stops without executing its actions.

Desktop execution failures are also returned to the planner and policy with the failed action, exact
exception, already-completed actions, and a fresh screenshot. The model corrects the same
objective in the current state; three execution failures stop for review. Failed actions may have partially executed,
so the model is told to inspect the screen instead of blindly replaying the original chunk.

For a generic model, `strict: false` allows conservative syntax repair (for example a bare
action array); it never invents missing coordinates. Set `strict: true` for the trained policy.
Endpoints without JSON Schema support require explicitly disabling `structured_output`; there
is no silent downgrade. Valid JSON does not establish CAD competence or task completion.
Restarting the server loads backend/config changes but closes its CAD sessions: save work first.

## Workspace and recovery

The assistant panel is resizable and the workspace adapts to narrow screens. Starter prompts,
collapsible step cards, a task status card, elapsed time, and completed-step counts make long
runs easier to follow.

Agent events remain in a bounded session history on the server. Reloading, reconnecting, or
switching tabs restores that history and the current lock/mode/pause state. Export downloads
the activity as JSON. A private `state/<session-id>/activity.jsonl` journal retains diagnostics
across shutdown (16 MiB cap; no screenshots), alongside a capped 1 MiB application log.
Saved native-tool projects survive session/server shutdown under `harness/projects/<id>/`.
The launcher offers recent saved parts. Pi restores the project conversation from its JSONL session tree.
This does **not** restore unsaved GUI edits, full application state, or the old live event stream.
Save manual changes separately before closing a session or restarting the server.

### Editable source, inspection and specifications

The Model panel's **Source & requirements** editor exposes `model.py`, `model.scad`
and `design-spec.json`. Stop the assistant to edit these yourself; save the file, then
build the model. Saving text does not alter saved geometry. Concurrent edits are rejected
instead of overwriting another writer's file. Drafts and successful revisions are separate.

Pi's upstream `read`, `write`, `edit` and `bash` implementations use custom filesystem/shell
operations scoped to `/work`. Bash and generated CAD Python have no host home, credentials,
other projects or network. They have a read-only FreeCAD runtime, an 8 GiB address-space
limit and 64 MiB file limit. Model inference and source builds have no fixed wall-clock
deadline; cancellation kills the sandbox process group. Bash can specify its own timeout.

| Tool | Purpose |
|---|---|
| `cad_build` | Build a Python/OpenSCAD entrypoint, validate in a fresh kernel, save source/spec/exports and open the revision |
| `create_path_body` | Draft a custom SVG path body with lines, Bézier curves and holes, for extrusion or revolution; no primitive required |
| `cad_inspect` | Named objects, paged actual faces, minimum distance/intersection and cross sections, with revision-qualified evidence |
| `cad_render` | All six directions, isometric or arbitrary camera; body isolation, highlighting and section clipping |
| `spec_read`, `spec_update` | Persistent requirements, accepted decisions, coordinate conventions, references, provenance and geometry links |
| `cad_checkout` | Recover current revision's source after changing the project base; protects unsaved source edits |
| `research_dimensions` | A separate Pi research session investigates missing dimensions and returns a compact cited report, assumptions and unknowns |

FreeCAD source uses the full Python/Part APIs and exports named shapes through `parts`.
`cad_paths.py` supports SVG path `d` data, not full SVG documents. Lines and quadratic/cubic
Béziers remain native; elliptical arcs use cubic approximation. See [the workspace guide](knowledge/source-workspace.md).
OpenSCAD keeps its original source and generates a mesh; its FCStd/STEP exports are explicitly
marked as faceted BREP. Contacting assembly meshes can be non-manifold, so `parts.zip`
contains separately audited STL files. Geometry validity does not establish fit.

Each source revision includes `source.zip` and `design-spec.json`. Specification versions,
user inputs and inspection evidence also persist separately, including between builds and
across Pi compaction. Verification evidence belongs to a revision; rebuilding makes prior
verification stale. Unknown source/input IDs and stale specification writes are rejected.
The researcher/modeler still has to interpret evidence correctly.

Dimension research sessions have research/image tools only: no CAD, shell, file-writing,
recursive delegation or direct user-question tools. The caller supplies the exact part,
missing dimensions and selected reference images. Each documented value must cite an
opened page/PDF and a matching quote. Their full Pi conversation and UI transcript remain
under `research-tasks/<id>`; the main agent receives only the report and supporting extracts.
The report includes a detail URL for inspecting the investigation. Stopping the parent
cancels its child session. Both use the selected model/provider settings.

Older operation-based projects remain editable with the tools below. Switching to source
uses a frozen copy of the saved FCStd as a base. After a source build, edit the source;
typed operations refuse to overwrite it.

### Typed native tools and saved revisions

With `agent.native_operations: true` (the default), each active CAD project runs a Pi SDK
session in a Node subprocess. Tools are generated from `server/operations.py: TOOL_DEFINITIONS`;
every call is normalized and validated before the kernel runs. Each successful build saves a
checkpoint. Parameters and named datums live in the workspace, so "make the walls thinner"
can be one `set_parameter`. The tool set is deliberately general:

| Tool | What it does |
|---|---|
| `create_body`, `fuse`, `cut` | one primitive (box, rounded_box, cylinder, cone, sphere) placed by `at` + `anchor` (corner / center / base) + `axis` (x / y / z). No Euler angles. |
| `shell` | hollow a body leaving `wall`; `open_faces` lists the faces that become openings (`["zmax"]` open-top box, `["ymin","ymax"]` a duct) |
| `hole`, `hole_pattern` | round holes into a named face (`xmin`…`zmax` or left/right/front/back/bottom/top). `u`,`v` are world coordinates on that face; the word `center` means the face center (`u="center+18"`); `depth` is a length or `"through"` |
| profile kinds in `create_body`/`fuse`/`cut` | `extrude` (2D profile + height), `revolve` (radius/height profile + angle), `pipe` (3D path + radius), `loft` (sections at z levels), `text` (raised lettering) |
| `fillet`, `chamfer` | by edge rule: all, vertical, horizontal, top, bottom, outer_vertical (FreeCAD/STEP/STL only; the OpenSCAD export notes them) |
| `mirror`, `polar_pattern` | fuse a mirror copy across a plane; copies of a cut/fuse around an axis |
| `import_reference`, `place_reference` | download or upload a STEP/STL, place it; every checkpoint then reports clearance and interference per part |
| `recall_facts`, `design_notes` | facts the agent itself extracted from pages in earlier work (`knowledge/learned_facts.jsonl`, with source URL and quote; nothing hand-written) and general design-rule notes (`knowledge/*.md`) |
| `research_images` | reference pictures from an image search, attached to the next modeling turn |
| `set_parameter`, `define_parameter`, `define_datum` | change or add a workspace parameter and replay; a datum `board` gives `board_x/_y/_z` for relative positions |
| `edit_operation`, `delete_operation`, `replace_operation` | change single fields of a saved operation by index, remove one, or rewrite one |
| `review` | advisory model review of the saved model against the request and brief |
| `inspect` | numeric bounds, solid wall bands per face, unused parameters |
| `brief` | write/replace the design brief (dimensions with origin: user / sourced / assumed) |
| `ask_question` | a question with 0-6 suggested answers (single or multi-select) and its own text-entry option; chat drafts are independent |
| `research`, `ask`, `finish` | web lookup, free-text question (also in-turn), complete with a summary |

Every checkpoint renders iso/top/front/right views (`view-*.png`, painter-rendered in the sandbox)
that are attached to the next modeling turn, shown in the Model panel and served under the
revision. `geometry.faces` lists the result's faces so a face the user selected in FreeCAD can be
located; the user's selection at send time travels with the message. Users can attach images or
STEP/STL files from the composer (`POST /api/sessions/{id}/attachments`). PDF drawings without
extractable text are rendered to page images for the vision model.
Parameters may be declared on any operation and unused ones are reported at finish, not rejected.
Faces and `through` depths refer to the body's axis-aligned bounding box (stock plus unrotated
fused primitives). Legacy v1 tools (`hollow`, `side_window`, `lid`, `cut_pattern`, position/rotation
primitives) still compile so saved ledgers replay, but they are no longer offered to the model.

Pi stores the complete conversation tree under `projects/<id>/pi/sessions/*.jsonl`.
Existing conversation records are imported once. `conversation.json` keeps UI metadata and a
durable inbox for messages Pi has not saved yet; it does not rebuild Pi's context each turn.
Reference images travel in their user messages, and image tools return real image content.
`view_image` can reread or crop original reference pixels after compaction. CAD tool results
include current measurements and rendered views. `inspect` reads the current workspace,
brief, research, references and selection, including projects resumed from older versions.

New messages during a run use Pi steering and are delivered at Pi's next safe boundary.
Stop aborts inference and pending tool work. Pause holds CAD tools before execution; it does
not discard the model's response. Pi handles compaction using the model's detected/configured
context window. There is no custom history trimming, synthetic recovery prompt, or native
step/failure budget unless an operation budget is explicitly configured. The default model
request timeout is disabled; an explicit positive value configures Pi's HTTP idle timeout.
The last request is recorded in `last-model-request.json` with image data replaced by hashes.

Requirement review is an optional advisory tool, not an engineering fit certification.
Cuts/unions form connected bodies; 2–8 finished bodies use a separated print layout. The executor
wires boolean operands and chunks large patterns without orphan cutters. Dimensions are arithmetic
expressions over named parameters; disconnected features, invalid dimensions and
non-material-removing cuts fail. FreeCAD output has native Part features and an editable parameter
spreadsheet; OpenSCAD retains parameter expressions. This is a CSG feature tree, **not a
constrained Part Design sketch tree**. Assemblies, imported mesh modification and arbitrary scripts are not native-tool capabilities.
The agent must explain unsupported requirements; visual-only mode remains available separately.

Each successful native operation creates a new revision containing `design.json`, `geometry.json`,
`model.FCStd`, `model.scad`, `model.step`, and `model.stl`, plus hashed `workspace.json` for operation
replay and optional `research.json`. Restores retain the ledger as well as the exports. Existing
recipe-only projects can be adopted without discarding geometry; semantic enclosure tools require
a body created through the new tool, while generic cuts/fuses and parameter edits support legacy parts.
The geometry report proves native solid
validity and records bounding dimensions, volume and material removed by cuts. It does **not** certify
all user requirements. STEP/STL exports are produced by FreeCAD; OpenSCAD tessellation may differ.
Source checksums record the compiler used for new builds; restored files retain their original geometry.

Failed builds do not change the project head and return the exact CAD error, failed arguments and
last successful state to the model. Schema-invalid repeats and equivalent failed geometry are blocked
before another kernel run. Read-only inspection remains idempotent. A rejected proposal does not mark
an earlier successful UI step as failed. Stop cancels pending generation and compiler work; steering is queued by Pi. A commit is recorded
before opening the viewport; a viewport failure cannot erase the saved revision. Restore appends a
new revision and leaves earlier files intact. Artifact downloads validate scoped paths and hashes.
Editors open private working copies, so Ctrl+S does not overwrite a saved revision.
The old whole-recipe generator remains available only through `native_operations: false` for
regression comparison. This is not an automatic fallback after a failed operation.

Viewport input conservatively marks possible manual edits. Choose **Use saved revision** to explicitly
base the next native edit on the saved recipe; existing open documents remain untouched. This does
not import/merge manual changes. Use visual-only mode to continue editing those GUI changes instead.

Native compilation requires the bundled FreeCAD runtime, `bubblewrap` and `prlimit`. The worker has
no host home, network or display and can write only its staging directory. Limits: 32 parameters,
64 features, bounded expressions/graph expansion, 4 GiB address space, 90 CPU seconds, 100-second
wall timeout, 64 MiB per export, 100 revisions/project, and a 5 GiB free-disk floor. The host GUI apps
remain ordinary user-privileged processes. Do not treat compiler isolation as a GUI sandbox.

- **Pause** finishes the current step and pauses before planning another; Resume continues.
- **Take control / Escape** cancels a pending model request or action delay immediately. An
  input operation already executing finishes before the viewport unlocks.
- **Manual / Auto** changes an active runner, including one waiting for a manual objective.
- A new message cancels stale model requests and pending action delays. Already executing input
  finishes, then the planner incorporates the message alongside the original task and history.
  "Continue" never becomes the raw step title. Stopped runs continue the current goal by default;
  **New task** starts a separate goal without clearing the CAD document.
- Transient model/network errors retry twice. Repeated actions/cycles are blocked even when
  screenshots change. Bare Escape remains available to exit an active drawing tool.
- Reviews judge the immediate operation, not the whole finished part after every click.
  Two uncertain screenshots no longer trigger a pause. Concrete failed approaches and uncertain
  observations have separate bounded recovery budgets. Reviewer outages have distinct errors.
- The agent waits for application content, then uses bounded screen settling after input.
  Normal windows fill the display; dialogs retain their size. A session window manager is
  required because dialog keyboard focus proved unreliable without one.
- Native and portable FreeCAD sessions load a fixed, read-only observer: selected subelements,
  active sketch/workbench, visible task text, object types and numeric constraints. This resolves
  ambiguous visual selection colors. Observation does not recompute/edit/save or certify geometry.
  A separate fixed mailbox opens only an owned working copy and fits its result view; it does not
  accept arbitrary paths/code or close existing documents.
- Ctrl/Shift pointer gestures, horizontal scrolling, Unicode/multiline paste and release-on-blur
  are supported. Invalid/manual tool input returns a visible error instead of silently disconnecting.
- Ending a session asks you to save work first. Losing the browser does not close the CAD app.

The app is paired with a separately served model. The trained model is intended for release
on Hugging Face; dataset preparation and model training are development workflows outside
this app. The harness does not read training artifacts or expose training status.

```bash
# Current managed application
systemctl --user status cadpilot-harness
systemctl --user restart cadpilot-harness

# Deterministic lifecycle regressions (no model/GPU needed)
harness/.venv/bin/python -m unittest discover -s harness/tests -v

# Browser checks against a separately started server on port 7802
CADPILOT_PROJECT_ROOT=/absolute/path/to/test-projects harness/run.sh --port 7802
harness/.venv/bin/python harness/tests/browser_check.py
# Opt-in actual model check; creates and closes its own empty OpenSCAD session
harness/.venv/bin/python harness/tests/live_model_check.py
# Exact UTF-8 paste and fixed read-only FreeCAD adapter (own disposable sessions)
harness/.venv/bin/python harness/tests/live_input_check.py
harness/.venv/bin/python harness/tests/live_observer_check.py
# Stock model creates an empty Body, independently checked via native state
harness/.venv/bin/python harness/tests/live_controls_check.py
# Natural-language full task + independent native-file grading, NOT a scripted demo
harness/.venv/bin/python harness/tests/live_native_task.py --app openscad
harness/.venv/bin/python harness/tests/live_native_task.py --app freecad
# Native-tools stock-model plate → dimensional edit → restore/reopen, independent file grading
harness/.venv/bin/python harness/tests/live_project_check.py --app freecad
harness/.venv/bin/python harness/tests/live_project_check.py --app openscad
# Independent reference-shape checks for a spacer and a bracket with cross-axis holes
harness/.venv/bin/python harness/tests/live_variety_check.py
# Local-browser search + manufacturer spec → native fan plate, independent geometry grading
harness/.venv/bin/python harness/tests/live_research_check.py
```

Runtime dependencies are pinned in `requirements-lock.txt` (tested Python 3.12). Install them
in the harness venv with `pip install -r harness/requirements-lock.txt`. Web research requires
`harness/.venv/bin/python -m playwright install chromium`; PDF reads require system
`pdftotext` (poppler-utils), bubblewrap and prlimit. Model tests need a healthy local endpoint.

## Release boundary

This remains a **local, single-user development workspace**, not a multi-tenant/public service.
Bind to loopback. Host validation, same-origin HTTP/WebSocket controls, CSP/frame protection,
bounded requests and per-session preferences reduce browser/input hazards. CAD applications
still run with your OS user's filesystem privileges; these controls are not a CAD process sandbox,
save/overwrite permission system, or authentication layer. Do not expose the port publicly.

Passing lifecycle tests is not evidence of modeling competence. Earlier GUI-only stock-model plate
trials failed; the new hybrid path passes independent plate/edited-plate geometry checks in both
FreeCAD and OpenSCAD. That demonstrates these native-tool tasks, not arbitrary CAD competence,
trained-policy improvement, or SFT transfer. The native tool checks live in `harness/tests/`.
Runtime visual reviews remain model judgments, and runtime solid checks are not task certification.

## Windows CAD (SOLIDWORKS / AutoCAD) later

The viewport only needs frames + injected input, so a QEMU/KVM Windows VM slots in behind the
same interface (VNC framebuffer instead of XTEST). The session manager grows a `VMSession`
sibling; the UI and agent loop do not change.

## Notes

* AppImages are extracted (`--appimage-extract`) so no FUSE or root is required.
* Headless operation needs `Xvfb` (`sudo apt install xvfb`); until then sessions open as Xephyr
  windows nested in the desktop session.
* Install `openbox`, `matchbox-window-manager`, or `metacity` for reliable session window focus.
  Metacity is available on the current machine. Its bus session is separate from the host desktop.


## Prompt evaluation

`tests/prompt_eval.py` sends nine fixed scenarios through the exact production message builders to the stock model and scores the decisions deterministically (`--variant baseline|current|both`). Baseline prompts live in `tests/prompt_baselines.py`. Results land under `runs/harness-checks/prompt-eval-*/`.
