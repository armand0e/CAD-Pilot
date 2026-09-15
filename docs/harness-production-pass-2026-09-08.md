# Harness hardening pass — September 8, 2026

## Scope and release decision

The reported raw-message step titles and two-review pauses were harness defects, not proof
that the model could not recover. This pass changes conversation planning, recovery, desktop
input, session lifecycle, security, native observation, and tests. It does not add training or
dataset progress UI. The model remains separately served and intended for Hugging Face release.

Autonomous CAD modeling is **not production-certified** by this pass. Stock-model full-part
trials have failed to produce a correct saved deliverable. Native GUI state is now available
for FreeCAD, but independently validated, reversible feature editing and runtime deliverable
verification are still missing. The application is a loopback-only, single-user workspace;
CAD processes retain the user's OS privileges.

Deployed to main port 7800 on September 8, final reload at 18:51:33 EDT, after confirming no CAD sessions
were open. Final health checks show supervision v2, healthy configured model endpoints,
and no remaining test sessions on 7802.

## Changes

- Conversational planning in Auto and Manual. The original task, user updates, action history
  and review history survive stop/continue. New task explicitly separates a goal without
  clearing the document. User text is not dispatched as a literal modeling step.
- Structured act/wait/ask/respond/complete plans with immediately observable expected results.
  Status questions can be answered without editing. Steering interrupts stale inference and
  pending input delays; an already executing atomic operation finishes and retains its history.
- Before/after review of the immediate operation. Selection and incomplete multi-input commands
  can be useful progress. Separate recovery budgets allow four blocked attempts or six uncertain
  observations, with bounded re-observation and different handling for unavailable reviewers.
- Input-history repetition/cycle protection remains independent of model success claims;
  Escape is available for exiting a tool. Exact validation and execution errors go back to the
  models, including any successfully executed prefix and a new screenshot.
- Fresh read-only FreeCAD state: selected objects/subelements, active document/sketch/workbench,
  constraints, numeric feature properties, visible task labels, and enabled button centers on
  the policy's 0..999 grid. Hidden/occluded MDI controls are filtered out. Native observations
  neither recompute nor edit/save the document. Snapshots older than five seconds or larger than
  64 KiB are rejected. Password fields are not exposed. No arbitrary code-execution endpoint.
- Real session window manager (openbox/matchbox/metacity). Dialog keyboard focus was unreliable
  without one; metacity already exists on this machine. The WM has its own desktop-bus session.
- Exact UTF-8 clipboard transfer on the session X display, including multiline code. Existing
  text clipboard contents are preserved. Uncertain pastes return an error rather than blindly
  falling back and duplicating text. Keyboard fallback preflights the whole string.
- Manual keyboard commands remain actual keystrokes; only real browser paste uses clipboard
  transfer. Ctrl/Shift pointer gestures, horizontal scrolling and release-on-blur/disconnect
  are handled. Invalid manual input returns a visible error.
- Session limits, private XDG directories, X server PID ownership checks, owned process-group
  cleanup, a 1 MiB application-log cap, and a 16 MiB diagnostic event journal. Native app sessions
  still cannot survive a harness restart; journals are not document backups or restored tasks.
- Host/origin checks for HTTP writes and WebSockets, frame/CSP protection, request limits
  including chunked bodies, and configured-model-ID health checks. No public-service auth or
  OS-level CAD sandbox is implied.
- Collapsible step details, quieter uncertain-review handling, explicit continuation, and
  clear execution-versus-verification labels. Default budget is now 80 atomic GUI operations.

## Verification

- 62 deterministic harness tests pass (planning/steering races, lifecycle, schema repair,
  repetition, false completion, observation, native snapshot freshness, input, protocol,
  security, and log limits).
- 40 data-pipeline and 19 training tests pass. No training run or corpus mutation in this pass.
- Browser desktop/mobile, reconnect/replay, escaping, and live viewport checks pass with
  production CSP enabled and no browser errors.
- Live stock-model OpenSCAD lifecycle check passed: two operations, reload, pause, takeover.
- With native button grounding enabled, the stock model created exactly one empty FreeCAD
  Body in two operations and completed. Native state independently confirmed the object type,
  count and absence of sketches/geometry. Artifact: `runs/harness-checks/controls-_7eq_zu7/`.
- Exact Unicode/multiline input and previous text clipboard preservation passed in real
  OpenSCAD and saved the exact test source. Latest artifact: `runs/harness-checks/input-oaj45eum/`.
- Fixed read-only FreeCAD adapter passed: actual selected Edge1, active sketch, 12 mm constraint,
  fresh updates, unchanged geometry/constraints, and a mapped native button click successfully
  leaving sketch edit. Artifact: `runs/harness-checks/observer-1xgyip53/`. The fixture is explicitly
  scripted for adapter validation; it is not counted as a model-generated CAD task success.

Full native benchmarks use `harness/tests/live_native_task.py`. The model gets a natural-language
60 × 40 × 8 plate/four 5 mm holes/8 mm offsets task and must save a new native file through the
GUI. A separate sandboxed grader checks the saved shape. No correct script/part is supplied
to the agent; missing files fail. Diagnostic activity and screenshots are retained.

| Trial artifact under `runs/harness-checks/` | Outcome |
|---|---|
| `native-openscad-kx784auo/` | Failed: comment/code and character-entry problems; no saved native file |
| `native-freecad-udsz2k4x/` | Failed: active rectangle tool confused with selection; no saved native file |
| `native-openscad-0sqe5wlv/` | Visible plate produced; save-dialog input failed; no saved native file |
| `native-openscad-y84zyigk/` | Failed: stock model repeatedly replaced code with prose/comments; guard stopped cycle |
| `native-freecad-ezu5s6tn/` | Failed: sketch selection misinterpreted; guard stopped repeated clicks |
| `native-freecad-dnkidu36/` | Debug run explicitly stopped while validating the observer; not a completed benchmark |
| `native-freecad-w1bq12ju/` | Native-state-enabled trial reached a fully constrained 60 × 40 mm sketch and pad entry; 660-second bound expired without a saved part; failed |

The button lookup was added after the last full trial started; its actual mapping is covered by
the adapter check. Full-task reliability must be re-evaluated on a larger held-out suite before
claiming improvement in completion rate. These trials are debugging evidence, not an unbiased
aggregate benchmark or proof of trained-model performance.

## Remaining release gates

1. Consistent end-to-end native deliverables across held-out tasks in both FreeCAD and OpenSCAD.
2. Runtime native geometry/file validation tied to task requirements, independent of the actor
   and visual reviewer. The existing isolated benchmark grader is not wired into user sessions.
3. CAD-aware reversible feature transactions, checkpoints and enforced save/overwrite approval;
   current no-delete/no-overwrite instructions are prompt guidance, not an OS security boundary.
4. Persisted projects/tasks and session recovery across process restarts, with storage retention
   controls. The per-session diagnostic caps do not bound accumulation across unlimited sessions.
5. Broader OS/application packaging, accessibility, model-endpoint and multi-user/auth tests.
   The current native observer is supported for detected native/portable FreeCAD launches only.

Use `harness/README.md` for commands, dependency pins and operational caveats.
