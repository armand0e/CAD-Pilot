# CAD workspace supervision

Sep 8, 2026. Direction: a collaborative CAD workspace, not a click-playback demo.
The live native application remains the user's document editor; the agent proposes and
executes small operations, inspects outcomes, and exposes uncertainty and recovery in chat.
Dataset preparation/training monitoring remains outside this app.

Superseding evening pass: see `harness-production-pass-2026-09-08.md` for conversation-aware
steering, revised recovery, window-manager/input fixes, the read-only FreeCAD state/controls
adapter, exact test evidence, and current release limitations.

## Implemented interaction loop

1. Observe the current desktop and select a local objective in the context of the full task.
2. Generate a schema-valid action. Production config allows one operation per visual check.
3. Check the proposal against actual executed input history. Reject a third consecutive
   equivalent action or a third 2–4-action cycle, including small coordinate jitter. A
   changing screen or a model's positive review cannot disable this guard.
4. Execute. Validation/desktop exceptions return exact feedback and successful-prefix history.
5. Allow the application to settle, then compare before/after screenshots with the planner
   model in a separate structured review. Record achieved/progress/blocked/uncertain with
   concrete visible observations. The next operation is replanned from the actual current state.
6. On failure or uncertainty, both planner and policy receive the observations and must
   reconsider the approach. Separate budgets allow four blocked or six uncertain observations;
   repeated blocked proposals also pause. Guidance sent while paused resumes through planning;
   it does not clear the executed-action guard. Take control stops the agent.
7. A planner's DONE is checked against the whole task. Unestablished completion triggers
   recovery/pause. Even a positive visual review is explicitly *not* native geometry or
   file validity verification.

The UI labels input batches **executed**, shows visual observations separately, and labels
the counter/bar as action-budget usage. It does not turn a mouse click into a green CAD
success claim. Activity export and reconnect snapshots preserve the review/recovery events.

## Evidence and limitations

- Deterministic regression recreates the reported failure: the same toolbar click adds
  changing screen content, and the mock reviewer even claims success. Only two clicks are
  injected; further repetitions are blocked, fed to both models, and paused after recovery
  repeats the mistake. Cycles, coordinate jitter, within-batch repeats, partial execution,
  manual steering, cancellation, malformed reviews, and false completion are also covered.
- Live stock-model OpenSCAD lifecycle check passed (two operations, reload, pause, stop).
- A bounded ten-operation FreeCAD plate probe reached sketch editing and rectangle-tool
  activation, and reported incorrect/uncompleted operations. It did **not** produce or
  certify a correct plate. Event artifact: `runs/harness-checks/supervision-freecad.json`;
  screenshot: `runs/harness-checks/supervision-freecad.png`.
- The probe exposed screenshots arriving before an asynchronous CAD transition finished.
  Config now uses bounded quiet-interval settling, and prompts explicitly treat current
  screenshots as newer evidence than prior review suggestions. This reduces a timing hazard;
  it is not a proof that all application operations have settled.
- The reviewer is another call to the same stock model, so actor/reviewer errors can be
  correlated. Visual status is advisory. The independent guard is deliberately conservative
  and may also interrupt legitimate repeated navigation; it does not blindly allow repeats
  based on a model claim. No broad automatic undo/delete recovery is implemented.

## Next layers for a full CAD copilot (not implemented by this change)

- Extend the new read-only FreeCAD state inspector with independent shape validity,
  measurements, explicit requirement checks, and comparable OpenSCAD inspection.
- CAD-aware change previews and reversible document transactions, with clear boundaries for
  user-owned edits, save/overwrite approval, and recovery checkpoints.
- Persistent project/task plans across server restarts (same-session continuation is implemented);
  completion backed by native
  validation and a saved deliverable, not screenshots alone.
- Trained-policy and held-out native-task evaluations, then bounded on-policy RL using the
  isolated training environment. Runtime model review must never substitute for RL geometry
  rewards or be presented as measured learning progress.

These layers should use the GUI policy and native inspection together. They should not
hardcode the mounting-plate task or silently replace CAD work with unrelated training UI.
