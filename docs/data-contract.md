# CAD VLA data contract (`cad-vla-1.1`)

Each line in the generated JSONL is one narrated, quality-filtered action span. The record is
both a general trajectory format and a chat-style SFT sample.

## Top-level fields

| Field | Meaning |
|---|---|
| `id` | Stable `<software>--<workflow-id>--<segment-index>` identifier |
| `split` | Deterministic workflow-level `train`, `validation`, or `test` assignment |
| `source` | Dataset, pinned revision, workflow, segment, FPS, and duration provenance |
| `task` | Domain, application, task text, deliverables, and rubric requirements |
| `assets` | Task overview, locally synchronized task inputs, and final deliverables |
| `observation` | Source video, pre-action time, screen dimensions, and optional extracted image |
| `intent` | Narrated local objective |
| `action_summary` | Natural-language description of the demonstrated transition |
| `actions` | Ordered normalized GUI target actions relative to the segment start |
| `quality` | Heuristic score and any retained warnings |
| `images` | Image paths in common multimodal-dataset form |
| `messages` | System/user/assistant SFT conversation; assistant target is strict JSON |

## Canonical actions

Coordinates use the entire recorded screen and are normalized to `[0, 1]`.

```json
{"type":"click","x":0.42,"y":0.17,"button":"left","dt":0.814}
{"type":"drag","x":0.61,"y":0.72,"end_x":0.66,"end_y":0.70,"button":"left","path_points":10,"duration":0.198,"dt":1.207}
{"type":"scroll","x":0.50,"y":0.50,"delta_x":0,"delta_y":-2,"dt":1.930}
{"type":"type_text","text":"25.4","dt":2.114}
{"type":"key","key_code":13,"text":"\r","modifiers":0,"dt":2.420}
```

`dt` is seconds after the observation timestamp. A `drag` is a reconstructed press → pointer
samples → release gesture: `x,y` is the press point, `end_x,end_y` the release point (schema 1.1;
gestures moving less than 4 px remain clicks, orphan drag samples are dropped and counted in the
report's `drag_reconstruction`). `source.platform` records the recorder platform so key codes can
be named later. By default the observation is 0.5 seconds before
the first retained action, avoiding state changes at imprecise narration boundaries. `type_text` is formed only from consecutive,
printable, unmodified (or shift-only) key-down events at most 0.75 s apart. Typing groups are
computed over the whole recording before narration spans are cut, so a span boundary never splits
a typed value such as `40`; the group belongs to the span in which it started. `merge-shards`
additionally re-joins values that older shards had split (`typing_merges` in the corpus manifest). Shortcut and control-key events remain explicit `key`
actions.

## Training views

For VLM supervised fine-tuning, consume `images` plus `messages`. For action-chunk or imitation
learning, consume `observation`, `intent`, and `actions` directly. The natural-language
`action_summary` is useful as an auxiliary next-state description, but it should not be placed in
the model's runtime prompt because it describes what happened after the observation.

The policy view is implemented by `cad1000 policy-view` (`cad-policy-1.0`, see
`docs/training.md`): first action or short chunk, integer `0..999` coordinates, stable key names,
strict JSON completion, and the summary confined to an `audit` block.

For production training, derive at least two additional views from the same contract:

- a planner view mapping task + visual history to the next `intent`;
- a policy view mapping task + current intent + screenshot to the next short action chunk.

This separation avoids teaching the low-level policy to invent long, unverifiable plans.

## Leakage and evaluation

Never resplit individual rows. Neighboring narrated spans share the same application state and
often the same geometry, so segment-level random splits would substantially inflate metrics.

Evaluation should combine:

- exact action-type and keyboard-token accuracy;
- coordinate error in pixels at the recorded resolution;
- task progress judged from later screenshots;
- native CAD file validity and feature-tree integrity; and
- satisfaction of the workflow's rubric requirements.

The held-out final deliverable is the strongest task-level signal, but format-specific CAD
inspection requires licensed application workers or trusted headless geometry conversion.
