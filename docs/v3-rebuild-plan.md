# Approved v3 rebuild

- Preserve checkpoint 225 and stop the v2 automatic chain.
- Version the action contract; implement modifier-aware pointer actions and consistent scroll.
- Regroup source events into atomic actions with timestamps, screen/focus/privacy boundaries.
- Capture true pre-action frames and retain every unambiguous accepted atomic action.
- Group shared project artifacts before assigning app-aware splits; freeze the split manifest.
- Build a representative sample first; validate schema, frames, labels and semantic examples.
- Use full-coverage sampling and app-stratified evaluation; forbid silent token truncation.
- Add closed-loop native-geometry checks for FreeCAD/OpenSCAD as transfer evaluations only.
- Rebuild all 597 workflows incrementally into new outputs, preserving old shards.
- Gate a fresh-base pilot and full training on audited immutable artifacts; retain reports.

Progress and evidence are recorded here as work proceeds. Training/data work stays out of the
harness UI. No release or synthetic-training-data generation is authorized by this plan alone.

## Added on-policy RL scope

The user explicitly requested an on-policy reinforcement-learning stage. Implemented a bounded
group-relative REINFORCE trial: the current adapter generates every GUI action, fresh attempts
at each task share a leave-one-out reward baseline, and each group receives one update before
its rollouts are discarded. A frozen SFT adapter supplies KL regularization. No external planner,
teacher actions, positive-example relabeling or offline replay enters this trial.

Task rewards are computed in a separate sandbox from native FreeCAD/OpenSCAD artifacts and
reference geometry. Missing/misplaced holes and wrong dimensions fail tested checks. Disjoint
dimension sets separate train/validation/test tasks. Start with blocks, advance to plates after
success, and stop a trial with no reward variance rather than claim learning. Promotion requires
held-out task comparison. This infrastructure is not evidence that RL training has succeeded.

## Semantic review adjustment

Manual review of expanded atomic examples found coarse narration that contradicted a later
action (e.g. a span labeled trimming includes a new line command). Recipe v3.2 preserves the
original narration in audit metadata and labels it as multi-action subtask context in model
prompts, retaining needed dimension values without asserting it names each atomic action.
It does not invent fine-grained instructions from target actions, which would leak the answers.
