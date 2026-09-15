# Harness diagnosis from the A100 fan-shroud transcript — September 11

Source: test project `runs/harness-checks/native-projects/d2ee1c5bbaf74cee` (7802,
native operations enabled, `qwen3.8-27b` as planner and native modeler). The
`chat.sqlite3` transcript, `attempts/`, `conversation.json` and `r0001..r0003` were
read directly; nothing below is inferred from the pasted chat alone.

Outcome of the run: three checkpoints (a 120×55×35 box, hollowed open-top, one
cylinder cut) and then four consecutive failed `cut_pattern` proposals, two of which
were byte-identical repeats. The run paused with "This operation still needs
correction." Nothing resembling a fan duct was produced.

## What actually went wrong, in order

### 1. The thinking budget truncated every modeling thought

`config.yaml` sets `planner.thinking_token_budget: 1024` and `reasoning_effort: low`
for the endpoint that also does native modeling. The transcript has 16 reasoning
blocks; 12 of them end mid-word at roughly 3,000 characters (≈1,024 tokens):

```
'...But the instructions say to proceed when I can make reasonable choices.\n\nLet'
"...Card length: 267mm (but the shroud doesn't need to be that long -"
'...So perhaps the correct orientation would be:\n- X = 120mm ('
'...With rotation ['
```

In every operation turn the model re-derives the same problem ("a 92 mm circle cannot
fit in a 55×35 face"), reaches the point of deciding what to do, and is cut off.
Constrained decoding then forces a JSON operation out of a model that never reached a
conclusion. The harness records every one of these as `thinking_done status=completed`;
truncation is invisible to the loop and to the UI.

With `temperature: 0`, an unchanged prompt plus a truncated thought is deterministic.
That is exactly why attempts 2 and 4 were byte-for-byte repeats of attempts 1 and 3:
the "Identical operation … NOT executed again" guard fired, was counted as a new
failure, and the run hit the four-failure pause. The guard worked; the model was
never given the capacity to change its mind.

### 2. The planner→native handoff is a 240-character string

`planning.py` caps `objective` at 240 characters for `decision=model`. The planner
spent a whole turn compressing its design into that budget (`"That's about 155
characters. Good."`). To fit, it invented concrete numbers the user never gave:
"120x55x35mm box, 92mm circular fan cutout centered on front face". The native loop
receives that as `requested_edit` and treats it as fixed. The model's own reasoning
shows the trap: "the requested_edit is what the system has already determined to be
the plan … maybe the system has already resolved this." The geometric impossibility
was noticed on every turn and never acted on because the contract said it was settled.

The user's actual specification (option 2 duct; screw slots 36 mm apart, 18 mm off the
card centreline) survived only inside the dialogue JSON and was never turned into a
design brief.

### 3. An invented fact became a requirement

The assistant told the user: "I found that the A100 PCIe is a dual-slot card (267 mm
long) with a 92 mm blower fan." The extracted research notes contain exactly two facts
(267 mm length; 250 W, one 8-pin) and no fan fact at all. The A100 PCIe is passively
cooled; there is no fan on the card, which is why people 3D-print fan shrouds for it.
The sourced/assumption separation exists in `research.json`, but the public answer is
never checked against it, so "I found" laundered a guess into a spec, and the 92 mm
number then drove three failed operations.

### 4. The tool contract fights the model instead of helping it

Each of these cost at least one wasted turn in this run:

- **Parameter declaration rule.** First proposal declared all seven design parameters
  up front (the natural thing to do) and was rejected: "Unused parameters: fan_dia,
  hole_dia, hole_offset, wall". The prompt's rule "declare a parameter ONLY on the
  operation that first uses it" is a compiler convenience, not a modeling concept.
- **Rotation semantics are undocumented.** The prompt says only "rotations=[X,Y,Z]
  literal degrees. Cylinder extends along +Z". It does not say the primitive origin is
  a corner (box) or base-centre (cylinder), that rotation is about that origin, or what
  axis +Z becomes after `[90,0,0]` (it becomes −Y, which is why the first fan cut had
  bounds Y −55..0 and missed the body entirely). The model burned three turns on
  `[90,0,0]` vs `[0,90,0]` arithmetic that a human would never do by hand.
- **`cut_pattern` grids only in XY.** The mounting holes had to go through a face
  normal to Y; the pattern axes are always X/Y, so the four holes could not be placed on
  that face at all. The model's "fix" put the cutters 2 mm inside the cavity.
- **`hollow` is open-top only.** The requested duct is open at the back. There is no
  way to say which faces are open, so the shell was wrong from operation 2 onward.
- **Absolute corner-origin coordinates everywhere.** Every placement is `shroud_L/2`,
  `shroud_H/2`, `60±46`. There is no face, centre or anchor vocabulary. Small models
  are demonstrably bad at this arithmetic under a token budget; the transcript is
  pages of it.
- **Error text is a traceback plus prescriptions.** The cut failure returns a Python
  traceback, a JSON bounds dump and "add real connected mounting material if needed;
  do not drop the required hole." The one sentence that would have fixed it is absent:
  "the cutter (Y 26..29) lies inside the cavity; solid wall material is at Y 0..3 and
  Y 52..55."
- **UI noise.** "Inspected r0001; no geometry changed." and "Fit and task requirements
  still need review." are emitted as assistant messages; they are harness status, not
  conversation.

### 5. The system prompts are long, defensive, and missing the facts that matter

The native modeling turn receives ~9 KB of system prose (`OPERATION_SYSTEM` 4.5 KB +
`DIALOGUE_RULES` 1.5 KB + research instructions 2.5 KB) and a ~32 KB user JSON
(dialogue, ledger, geometry, the last error, and up to four full research pages
re-sent on every operation). Most of the prose is prohibitions ("never", "do not",
"is not certification") and product-specific guidance about lids, side windows and
Pi cases. It contains no coordinate-frame diagram, no statement of primitive origins,
no worked example of a complete small part, and no description of what a good next
operation looks like. For a sub-100B model this is a wall of rules with the two facts
it needed (rotation axis mapping; where the walls are) left out.

The 52 KB `anyOf` operation schema (29 variants) is sent as a strict grammar on every
turn. It works on local vLLM but means tool choice is enforced by grammar, not
understood by the model, and any schema change is a grammar recompile.

## What to change, in priority order

1. **Stop truncating thought.** Raise the native-turn thinking budget (4–8 k) or
   drop hidden thinking on operation turns and add a bounded `plan` string field
   (≤600 chars) to the operation JSON before `tool`, so the reasoning is visible,
   cheap, and survives into the next turn's context. Detect reasoning truncation
   from the stream and surface it as `thinking_done status=truncated`; a truncated
   turn must not count toward the four-failure budget.
2. **Remove the planner→native compression.** The model that models should be the
   one holding the conversation. For native work, pass the dialogue and let the
   modeling loop write its own design brief (a short structured object: parts,
   dimensions, sourced/assumed flags) as its first operation. No 240-char string.
3. **Ground "I found".** Any public answer sentence that asserts a measurement must
   match a fact note or be rendered as an assumption. The `validate_notes` machinery
   already exists; apply it to the answer text before it is shown.
4. **General tool surface v2** (replacing hollow/side_window/lid templates, keeping
   cut/fuse primitives for the general case):
   - `add_box` / `add_cylinder` with `anchor` (corner|center) and `axis` (x|y|z); no
     Euler angles for the common case.
   - `hole(body, face, u, v, diameter, depth|through)` and `hole_pattern` in
     face-local coordinates; faces `xmin..zmax` with `front/back/left/right/top/bottom`
     defined once in the prompt.
   - `shell(body, wall, open_faces=[...])`.
   - `define_parameters` allowed at any time; the unused-parameter check becomes a
     finish-time warning, not a rejection.
   - `inspect` returns per-body wall bands and face extents, not just a bounding box.
5. **Error messages in words.** One sentence stating where the cutter is relative to
   solid material, then the numbers. No tracebacks, no prescriptions.
6. **Rewrite the modeling prompt** to ≤1.5 KB: coordinate conventions, primitive
   origins, one worked example (plate with four through-holes), and a generated tool
   reference. Move certification disclaimers and product-fit policy out of the model
   prompt and into harness-generated user-facing text.
7. **Trim per-turn context.** Send research pages once (or on request), and send the
   last error with the failing proposal only, not the whole ledger and geometry twice.

Items 1–3 are small changes in `config.yaml`, `agent.py` and `planning.py`. Item 4 is
the real work and is the same request the handoff already records: "The user objects
to workflow-specific tools." Items 5–7 fall out of item 4.

None of this was implemented in this pass. The running services and the saved test
project were not touched.

## Implemented the same day (September 11, afternoon)

All seven items above were implemented on the test service (7802). Main 7800 was not
restarted. 199 harness tests pass (183 existing + 16 new in `tests/test_tooling_v2.py`).

1. **Thinking budget.** `agent.native_reasoning_effort: medium` and
   `agent.native_thinking_token_budget: 4096` apply to modeling turns and the requirement
   review; planning keeps the endpoint's `low / 1024`. The stream now reports
   `thinking_done status=truncated` when reasoning stops near the budget without sentence
   punctuation (`reasoning_truncated()` in `agent.py`), and a truncated attempt does not
   count toward the four-failure pause. Every operation JSON carries a `plan` string that
   is streamed to the UI and echoed back as `previous_plan` on the next turn.
2. **No 240-character handoff.** `decision=model` objectives may be 2000 characters and
   are passed as `planner_note`; the modeling loop reads the whole dialogue and writes a
   `brief` (persisted in `conversation.json`, echoed on every turn, given to the reviewer).
3. **Grounding.** `server/grounding.py`: measurements in `ask`/`respond`/`finish` text must
   appear in an extracted research fact or a user message. A reply that presents such
   numbers as found gets one rewrite request; anything still unsupported is labelled by the
   harness ("Unverified figures in this reply ..."). Sentences the model already labels as
   assumed are exempt.
4. **Tool surface v2** (`server/operations.py`): `create_body/fuse/cut` with `at`, `anchor`
   (corner|center|base) and `axis` (x|y|z) instead of position/rotation; `shell` with
   `open_faces`; `hole` / `hole_pattern` in face coordinates with the `center` keyword and
   `depth: "through"`; `brief`; parameters declarable on any operation with unused ones
   reported at finish; `inspect` reports numeric bounds and solid wall bands per face.
   Legacy `hollow/side_window/lid/cut_pattern` and position/rotation primitives still
   compile for saved ledgers but are not offered to the model.
5. **Errors in words.** Build failures return the kernel's message only (traceback stays in
   `compiler.log`). A cut that removes nothing says where it is relative to the body and the
   solid material bands along each axis through its center. Holes that do not fit a face are
   rejected before the kernel with the face extents. New: `geometry.overlapping_cuts` reports
   feature cuts that overlap an earlier feature cut by more than 20% (cavities excluded).
6. **Prompts.** `OPERATION_SYSTEM` is 3.9 KB with coordinate conventions, a tool reference
   and one worked example; `DIALOGUE_RULES` 0.6 KB; research instructions 0.9 KB.
   Certification language moved to harness-generated text.
7. **Context.** Full research page text is sent only on the turn after a lookup; per-cut
   volumes and mesh audits stay on disk; error feedback carries the failing proposal and
   error, not the workspace state twice.

### Live result (stock `qwen3.8-27b`, disposable FreeCAD session on 7802)

Evidence: `runs/harness-checks/a100-live-2PDdFgGw/` (events.log, activity.json, frame.jpg);
project `c870b6b2739c4d17` under the test project root, head `r0009`.

The same two user messages as the failed transcript were replayed. Timeline after the
reply: research (1 call) → brief → create_body → two fan cuts → a slot cut that hit the fan
opening, explained in words and repaired on the next turn → second slot → inspect → review
`revise` (slot offsets) → two `replace_operation` repairs → inspect → review `revise`
(missing mirrored slots) → two cuts → inspect → review `satisfactory` → finish, in 820 s
with 9 checkpoints, 3 rejected proposals and zero repeated operations. The finish message
listed its assumptions and the harness appended the unverified-figure caveat.

What it does **not** show: the design is a 130×100×3 plate with two 80 mm openings 36 mm
apart, which overlap each other, plus four slots. That came from an assumed 80 mm fan and
from the replayed reply ("option 2 ...") answering a different clarification question than
the one this run asked. The reviewer (still on the 1024 budget in that run, now fixed)
accepted it. The overlap report added afterwards would have flagged the two fan openings
(47% mutual overlap). Loop mechanics are fixed; design judgement and fit are not certified.

### Two more live runs after the reviewer-budget and overlap fixes

- `runs/harness-checks/a100-live-xcu8dv2i/`: the model researched twice, then asked to
  confirm card dimensions, explicitly labelling its "~267×111×50 mm" figure as an
  assumption. The probe script ended this run by mistake right after sending the reply
  (a test-harness bug, not a harness bug); no conclusion from it.
- `runs/harness-checks/a100-live-j8wru8s0/`: the model wrote "The A100 PCIe is a passive
  card (no built-in fan)" and asked fan size and shroud style. After the replayed reply it
  recognised that fans 36 mm apart centre-to-centre cannot be a common size and asked one
  focused question about the fan diameter instead of assuming. The probe stops at a second
  question by design, so no geometry was built. This is the intended behaviour for a
  genuinely under-specified request; it also shows the 36 mm figure being read as fan
  spacing rather than screw-slot spacing, which the user's message leaves ambiguous.

Across the three runs the failure modes from the original transcript did not recur:
no truncated modeling thoughts, no repeated identical operations, no 240-character
objective, no "I found" claim without a fact, no cut aimed into a cavity that the
model could not interpret. Task-level reliability on the handoff's benchmark list is
still unmeasured under the new contract.

## Second pass, same evening: search, questions, context drift (Pi 3B case transcript)

The user's Raspberry Pi 3B case transcript (project `55040db82ffd49ee` on the test root)
showed four more bottlenecks; all four are fixed and covered by tests (203 pass).

1. **Web search returned the same junk for every query.** Measured live: DuckDuckGo's
   HTML and lite endpoints answer HTTP 403 from this host; Bing with `setmkt=en-US`
   ignored the query and returned the identical result set for every search (which is
   exactly what the transcript shows); Brave worked but its result DOM moved from
   `#results` to `#mixed-main`, so zero rows parsed. Fixes in `browser_research.py`:
   Brave first with the current selectors, plain Bing second, DuckDuckGo last; up to 20
   rows per engine; engines are queried in turn and merged until 12 unique pages; an
   engine that returns HTTP errors or no results is not retried with the identical
   query. `research.py` keeps up to 15 results per search with their engine, the agent
   retains 40 sources and sends 600-character snippets. The call budget is gone
   (`research.max_calls: 0`); the prompt now asks for 3-8 keyword queries and reading
   the best result. Verified: "raspberry pi 3b mechanical drawing pdf" now returns the
   official mechanical-drawing PDF from datasheets.raspberrypi.com in the first results.
2. **"All dimensions assumed; no verified source available."** The run never opened a
   page because every search was junk. Besides the engine fix, the `brief` result now
   carries `research_status` (facts, pages read, unread result titles/URLs); when the
   brief says "assumed" with zero pages read, the model is told to read the most
   relevant result first. The prompt says a search alone reads nothing.
3. **Questions with suggested answers, without ending the turn.** New `ask_question`
   operation (`question`, 2-6 `options` with label/description, `multi_select`). The
   loop emits a `question` event and waits in-turn (`_await_answer`); the UI renders a
   card with radio/checkbox options, "Use selected" for multi-select and "Something
   else" that routes the composer text to the question. A typed message while a question
   is open answers it (`submit_intent` → `answer_question`), the ws `answer` command
   carries clicks, the snapshot restores an open question on reload, and the dialogue
   records question/answer pairs with reply links. The planner's `ask` decision and the
   modeling `ask` use the same path, so nothing pauses the run anymore.
4. **Context drift between operations.** The model re-derived the whole port layout on
   every turn, the reviewer's overlap report cited kernel feature ids (`Op022`) that map
   to nothing the model can see, `through` holes on a shelled body drilled the far wall
   too, and the overlap metric counted a hole crossing the empty cavity as overlapping a
   mounting hole. Fixes: the ledger now keeps each operation's `plan`; `workspace.operations`
   lists every saved operation with its plan and resolved geometry in mm (e.g.
   "box x 5..23 y -1..3 z 2..10"); kernel feature ids in errors and overlap reports are
   translated to "operation 11 (hole)"; `through` on a shelled body cuts that wall only
   and refuses open faces; overlap is measured on removed material, so cavity crossings
   are ignored; the prompt tells the model to write decided layouts into the brief before
   cutting. Planning turns get `reasoning_effort: medium` / 3072 tokens (1024 truncated
   the follow-up planning turns in this transcript).

### Findings from the first live Pi-case run on the new code (temporary backend 7803)

Evidence: `runs/harness-checks/pi-live-5orbsxk0/`. With the fixed engines the model found the
official mechanical-drawing PDF and two secondary pages on its own and built tray, shell and
mounting holes (3 checkpoints) before failing. Three more defects surfaced and were fixed:

- **Sentences sent as searches.** "Read the official Raspberry Pi 3B mechanical drawing to
  extract…" was passed to the search engine (Bing returned dictionary entries for "read").
  A query that starts with read/open/fetch/visit is now refused with the recent result URLs
  and the instruction to pass a URL.
- **A model timeout ended the whole run.** After page reads the modeling turn took 98-217 s
  (two 4096-token thoughts were truncated at ~110 s) and the 180-second limit raised a
  run-ending error. A timeout is now a correctable failure fed back to the model ("decide in
  fewer words; see workspace.operations"), and only the latest read page (7,000 chars) is
  sent in full on the turn after a lookup.
- **Unreadable PDF retried three times.** The official drawing has no extractable text
  (a scanned/vector drawing); the same URL was retried after the 90-second window. Failed
  page reads are now not repeated for an hour within a run.
- Extraction returned zero facts from the pages it did read (a drawing with scattered numbers
  and a generic hardware overview): that is honest behaviour, not a harness bug, and it is
  why the reply later said "assumed". The brief nudge now fires whenever nothing has been
  read, whatever the brief's wording.

### Second live Pi-case run (all fixes above, temporary backend 7803)

Evidence: `runs/harness-checks/pi-live-ny89012o/` (events, activity, frame, copied project).
2,388 s, 19 saved checkpoints, no model timeouts, no repeated operations, zero questions
asked. The model read a page with the real Pi mounting pattern (58 × 49 mm, 2.7 mm holes,
3.5 mm edge offsets) and rewrote its brief as "CONFIRMED (sourced …)". Overlap reports
triggered two correct `replace_operation` repairs. It ended at the four-failure pause while
reworking port cutouts: the expression error did not say which expression was rejected and
the duplicate-parameter error did not say which operation had declared the name; both
messages now name the offender (206 tests). Remaining weakness is knowledge, not tooling:
the model changed its mind three times about which board edge carries the audio jack, and
no text page it read states the port positions. That is the case for `ask_question` or a
user-supplied drawing, which the harness now supports and which this probe did not exercise
(the model chose not to ask). Journal note: the on-disk activity journal hit its 16 MiB cap
after ~40 minutes of streamed reasoning; in-memory activity was unaffected.

## Prompt-engineering pass (same evening) with a measured eval

`tests/prompt_eval.py` is an opt-in live evaluation: nine fixed scenarios (fresh Pi task,
search results present, tray built with port positions unknown, fresh A100 request, a cut
that missed the wall, a parameter conflict, a finish decision, and two planner turns:
"I don't see any port slots" and "are you sure this is the right layout?") go through the
exact production message builders (`_native_messages`, `_planner_messages`) to the stock
model, once per prompt variant at temperature 0, and deterministic checks score the decision
(tool choice, keyword vs sentence queries, URL reads, questions with options, cutter meets a
wall band, compiles, admits assumptions). Baseline prompts are frozen in
`tests/prompt_baselines.py`.

What changed in the prompts:
- Modeling prompt rewritten as a numbered decision order (error → question → research →
  brief → build), a geometry section with the wall-opening recipe, and six JSON examples
  including a keyword search, a URL read, an `ask_question` with options and a wall cut.
  New rule: connector openings for a real product are never cut at guessed positions; read a
  page or ask (standard layout / wait for measurements / leave them out).
- Planner prompt split: a short core (model / respond / ask with options / research /
  complete) and GUI rules appended only in visual mode. The planner's `ask` now carries
  `options`, rendered as the same answer card. `_model_context` no longer advertises the
  retired hollow/side-window/lid tools.
- Notes extractor asks for the shortest verbatim fragment containing the number (long quotes
  were being rejected). Reviewer gets a four-point checklist (stated dimensions, implied
  features, openings inside wall bands and not overlapping, brief vs facts).

Results (`runs/harness-checks/prompt-eval-k082qn0q`, then `-njfoaqgi` after the connector
rule): baseline 0.70 → new prompts 0.79 on the first run; the connector rule then moved the
two weak scenarios from "cuts a port at an assumed position" to "builds the lid first" and
"reads a page for the positions", and the planner turn after "I don't see any port slots"
went from a sentence sent to search (baseline) to a `model` decision carrying only sourced
numbers. The final full run of the new prompts is recorded below.

Final like-for-like runs with the same scorer (`prompt-eval-3dbmrj7h` baseline,
`prompt-eval-eihwbhg8` new prompts), stock `qwen3.8-27b`, temperature 0:

| scenario | baseline | new |
|---|---|---|
| S1 fresh Pi task | brief without sources 0.4 | keyword search 1.0 |
| S2 results present | reads URL 1.0 | reads URL 1.0 |
| S3 ports unknown | cuts assumed port 0.2 (98 s, truncated) | builds the lid first 0.5 (27 s) |
| S4 fresh A100 | asks "what does a100 refer to" 1.0 | asks fan size with options 1.0 |
| S5 cut missed wall | corrected 1.0 (85 s) | corrected 1.0 (55 s) |
| S6 parameter conflict | set_parameter 1.0 | set_parameter 1.0 |
| S7 finish decision | rewrites brief 0.3 (71 s) | reads a page for positions 0.8 (27 s) |
| P1 "no port slots" | sentence sent to search 0.4 (50 s) | model, sourced numbers only 1.0 (28 s) |
| P2 "is this layout right?" | admits assumptions 1.0 | admits assumptions 1.0 |
| **mean** | **0.70** | **0.92** |

The heavy scenarios also run two to three times faster because the model stops re-deriving
the layout each turn. This is a nine-scenario single-sample eval at temperature 0, not a
statistical claim; it is meant to be extended with each new failure seen in live transcripts.

## Capability pass (September 12): what "vibe modeling" needed, and what was built

Asked what was missing for the model to model "anything", the answer was: it cannot see its
model; its vocabulary is boxes and holes; real dimensions live in drawings and reference
models, not web text; there is no fit reasoning between parts; no design knowledge; no way for
the user to point or show a picture; no benchmark; and no training data in the target format.
All of it except throughput batching is now implemented:

- Rendering: `render_views` in the sandbox worker rasterizes the exported mesh into four
  orthographic shaded views (painter's algorithm with PIL polygons, numpy transforms). The
  first attempt hung: numpy's OpenBLAS spins when the sandbox's 4 GiB address-space limit
  prevents its buffer reservation. The limit is now 8 GiB with one BLAS thread. Views are
  optional revision files, shown in the Model panel and attached to the next modeling turn.
- Vocabulary: `design.py` grew extrude, revolve, pipe, loft, text, fillet, chamfer, mirror,
  rotate and reference feature kinds with per-kind option validation; `cad_worker.py`
  builds them (pipe via makePipeShell with rounded transitions; the sphere/cylinder fallback
  must never call removeSplitter, which hangs OCC; fillet/chamfer by edge rule with a worded
  failure); `operations.py` exposes profile kinds inside create_body/fuse/cut and the
  tools fillet, chamfer, mirror, polar_pattern (rotate copies), place_reference.
- References: `import_reference` downloads a public STEP/STL through the pinned fetcher or
  names an uploaded file; `place_reference` adds a non-result reference feature; the worker
  reports per-part clearance and interference against it.
- Drawings and pictures: PDFs without text are rendered to page images (pdftoppm in the
  networkless sandbox) and attached; `research_images` fetches up to four pictures from an
  image search, re-encoded to bounded JPEGs; user uploads go through the same store.
- Knowledge: a hand-curated footprint library was written first and then removed when the
  user pointed out it overfits to known products; the agent must find dimensions itself.
  In its place, `remember_facts` stores every fact the agent extracts from a page it read
  (statement, verbatim quote, URL) in `knowledge/learned_facts.jsonl`, and `recall_facts`
  searches that store across projects. Three general design-note documents remain.
  The vase grader was corrected for the same reason: it now measures hollowness instead of
  requiring a particular construction.
- Pointing: the FreeCAD selection at send time is mapped to operation numbers and to
  `geometry.faces` (index, normal, bounds), so "this face" has coordinates.
- Benchmark: six tasks with deterministic graders (`tests/benchmark_run.py`), a scripted
  question answerer, verified on a built plate and on a negative control.
- Training data: `scripts/export_trajectories.py` exports trajectories as quarantined JSONL
  (10 projects exported from the current roots).

Live check on 7802 (`runs/harness-checks/benchmark-cre8xtna`): plate_holes passed in 84 s
(2 checkpoints, 1 rejected proposal); the vase task built a single hollow `revolve` with a
2 mm wall in 167 s and passed once the grader measured hollowness instead of construction.
Both revisions carry the four rendered views. The benchmark's remaining four tasks have not
been run live yet.

## Root-cause rebuild (September 12)

The user's observation that every unmentioned small edit broke ("make the walls thinner")
was traced in the Pi Zero session: the right move was one `set_parameter`, but parameters
lived inside operations, so the model rewrote operation 1, lost the declarations, and then
maintained outer sizes as hand-computed constants. Root causes and what replaced them:

| root cause | replacement |
|---|---|
| parameters declared inside operations | workspace-level parameters and named datums (`name_x/_y/_z`); operations only reference names; v1 ledgers migrate |
| edits as whole-operation re-emissions | `set_parameter`, `edit_operation(index, changes)`, `delete_operation`; `replace_operation` kept |
| screenshot planner in front of every message | native projects run one persistent modeling loop; the planner remains only for visual-only sessions |
| guards that turn edits into failures | review advisory, no inspect gate, failures and timeouts end the turn with a message |
| JSON-grammar output with a `plan` field | native reasoning + content + function calls; content is the user-facing message; tool calls stream into the timeline |
| state re-serialised each turn | the transcript (user, assistant with tool_calls, tool results) is the memory; a workspace state message with images is appended each turn but never stored |

The user then asked why the model produced JSON at all; the vLLM server was already
running a tool-call parser, so the switch to native function calling was made the same day.
Prompt evaluation runs through the same transport. The composer was redesigned to the
user's screenshot (input row, toolbar with +, Web, model, effort, context ring; paste/drop).

Live check after the rebuild (`runs/harness-checks/edit-live-36v_cm14`, stock model on 7802):

| request | what the agent did | time |
|---|---|---|
| open-top box 60x40x30, 2 mm walls | stated the numbers tagged (user), create_body + shell, reported the 56x36x28 cavity | 38 s |
| "make the walls thinner, like 1.2 mm" | one `set_parameter wall 1.2`; reported the new 57.6x37.6x28.8 cavity | 16 s |
| "how thick are the walls now and what is the inside size?" | answered in text, no geometry | 8 s |
| "add a 10 mm round hole through the middle of the bottom" | one `hole` on zmin at L/2, W/2 | 19 s |

Final volume 9531.9 mm³ matches 60·40·30 − 57.6·37.6·28.8 − π·5²·1.2. The only rejected call
was the first body without parameters ("Use 1..32 named numeric parameters"), a legacy
minimum that is now 0.
