# Hand-authored CAD seed demonstrations

These are **assistant-authored, tool-executed examples**, not human-expert CAD
trajectories, not on-policy rollouts, and not a production-quality corpus by themselves.

Five episodes cover a four-hole plate and parameter-preserving revision, a spacer,
a vertically rounded plate, a hollow two-piece vented enclosure, and a missed
cutter followed by correction while preserving an existing saved revision.
They comprise seven successful builds and one intentionally failed build.

Run without training:

```sh
harness/.venv/bin/python training/curated/replay.py
```

Each invocation creates an isolated `runs/curated-cad/replay-*` directory containing
real tool messages/errors, immutable native revisions, artifact/source hashes,
independent geometry checks and a manifest. Failed/interrupted replays do not get
`replay_complete: true`. Nothing is appended to the ongoing v3 rebuild or SFT data.

The recipe compiler produces FreeCAD, OpenSCAD, STEP and STL. The verifier uses a
separate kernel-edge-fillet construction for rounded geometry, compares the entire
FreeCAD/STEP shape with the specification, renders the OpenSCAD source, checks mesh
topology/volume, then edits a spreadsheet parameter, saves, reopens and recomputes.
The mesh API check runs in its own process because this bundled FreeCAD runtime
exhibits order-dependent Mesh diagnostics after BREP booleans. Production also has
a deterministic audit of the serialized binary STL; its scope is explicit.

## Training boundary

The trace's `compile_design` tool names the real internal `Project.prepare` +
`Project.commit` operation. It is **not** the live planner's `decision=model` JSON
contract, nor the current visual policy's `{actions: [...]}` contract. A future
training adapter must map the verified recipe/error pairs to the native generator's
prompt/response format, and verify loss masks on the actual model tokenizer.
The brief assistant descriptions are author annotations for that adapter, not
screenshots or fabricated execution evidence. No automatic training adapter exists
for these traces yet.

Every trace remains quarantined with `training_eligible: false`. The deliberately
bad tool call has `supervise: false`; it is a recovery context, not a correct target.
Grader output is stored separately from the pre-action model context. Cases sharing
the four-hole-plate family must never cross train/eval splits. This small regression
set is not a held-out benchmark, and successful replay does not measure stock-model
success rates or transfer to GUI use.

Before using these for training: obtain CAD expert review; add tougher feature and
manufacturing checks; build the explicit native-generator adapter; use family-grouped
splits; and obtain separate authorization to start training. For computer-use SFT,
author and actually execute additional GUI demonstrations with before/after frames
and the existing action contract. For on-policy RL, collect fresh model rollouts;
do not relabel these teacher traces as on-policy data.

The generic enclosure is **not a Pi 3 case**. Device variant, connector envelopes,
mounting features, material allowances, retention forces, cooling and physical fit
remain separate engineering requirements. No thermal/printability certification is
implied by passing a geometry check.
