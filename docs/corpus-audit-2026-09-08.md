# Corpus audit — 2026-09-08

Verdict: the files are structurally healthy, but the current recipe is **not yet a
high-quality release corpus**. Fix alignment/action coverage and split design before treating
the full run as a release candidate. This was a review: existing shards, splits, and training
configuration were not rewritten, and the automatic full-run service was not paused.

## Scope and evidence

The build was still running. These are explicitly different snapshots:

| Audit layer | Scope | Evidence |
| --- | --- | --- |
| Shard/source/schema/image audit | 566 workflows; 287,374 rows; 2,950,877 neutral actions | `runs/corpus-audit-2026-09-08/quality.json` |
| Isolated merge using the production recipe | 569 workflows; 290,349 rows; 3,918 typing joins | `runs/corpus-audit-2026-09-08/corpus/manifest.json`, `merge.log` |
| Derived trajectory view | 6,221 windows; 275,763 supervised steps | `runs/corpus-audit-2026-09-08/trajectory/report.json` |
| Merged alignment/action audit | All 290,349 merged rows | `runs/corpus-audit-2026-09-08/alignment.json` |
| Actual image processor and loss masks | Shortest train window per app, all 10 apps | `runs/corpus-audit-2026-09-08/processor.json` |
| Full JPEG content hashes | 572 workflows; 291,958 frames; no mismatches | `runs/corpus-audit-2026-09-08/frame-integrity.json` |
| Actual tokenizer budget planning | All 5,533 train and 256 validation windows | `runs/corpus-audit-2026-09-08/tokens.json` |

The initial snapshot checked all four JSONL checksums per shard, IDs, expected workflow splits,
pinned source revision, nonempty tasks/intents, action time order, coordinate bounds, every
referenced image header, frame counts, and screen/image aspect ratios. No violations found.
2,527 deterministic train images were fully decoded; none were unreadable or near-uniform.
Source narration was available for 518 shards and matched recorded intents/annotation bounds.
There were no duplicated workflow IDs or exact video OIDs crossing splits.

Real-processor checks matched planned token counts exactly and supervised only the expected
assistant JSON plus end tokens, never image tokens. No train/validation windows or trailing steps
were dropped for budget; maximum lengths were 55,039/54,973 tokens under the 57,344 cap,
including the updated explicit action prompt. All 33 pipeline, 15 training, and 15 harness
tests passed. This establishes implementation checks, not semantic correctness of every label.

## Findings, in priority order

1. **Shared project artifacts cross train/validation/test boundaries.** Seven exact CAD-file
   OID groups span splits. These are not duplicate videos; component/assembly and related
   project families can still contaminate evaluation. For example AutoCAD train workflow
   `1e68fec3-97fd-4594-95fa-2060a849fe57` and Revit Architecture test workflow
   `080f4954-839d-44a8-83e6-fadc890e9eba` share `ARCH_PLANS_-_900_SQ_YRDS_Day_02.dwg`.
   Six other groups involve NX parts/assemblies. Group connected project families before
   splitting; distinguish ubiquitous starter templates from project-specific shared files.
   Do not tune against the test set while rebuilding this split.

2. **The training recipe drops all drag targets and most recorded actions.** The 566-shard
   policy projection contains 273,061 targets: 142,586 clicks, 47,583 keys, 52,204 scrolls,
   30,688 typed strings, and **zero drags**. `drag_mode=exclude` rejects 14,244 rows, despite
   80,525 neutral drag actions. Default `max_chunk=1` supervises only the first action per
   narrated span: about 9.25% of recorded neutral actions. Later typing, constraints and
   geometry operations often go unsupervised; trajectory history also omits those executed
   intermediate actions. This is an intentional first-action baseline, not full interaction
   coverage. Prefer action-aligned screenshots/steps and explicitly validated drag inclusion;
   simply increasing chunks would pair later actions with stale screenshots.

3. **Legacy typing repair is not reliably aligned with frames or current preprocessing.**
   `merge_split_typing` runs on every shard without a preprocessing-version distinction,
   estimates the previous typing end from string length, and does not require adjacent
   annotation indices. The initial scan found 3,899 pairwise merge candidates; 2,715 have
   first-keystroke gaps greater than 0.75 s and one crosses skipped segments. These are
   suspects, not a count of proven wrong joins. In the actual merged view, 3,131 retained
   policy rows now have their first remaining action more than one second after the unchanged
   screenshot; the original snapshot had none. Repair removes the leading action but does
   not recapture its frame. Replay of retained SOLIDWORKS workflow
   `01a90368-0e9d-420b-99f2-0e3ec22c1638` makes eight repairs but still differs from current
   raw-event grouping on two rows: segment 58 ends in `1` instead of grouped `12`; segment
   156 still begins with a split `2`. This proves mixed preprocessing remains, not that every
   split digit is visually incorrect. Regenerate affected rows from raw events with explicit
   typing boundaries, version the recipe, and recapture observations when targets change.

4. **Scroll labels lose meaning.** In the merged view, 125 scroll targets are `(0,0)`;
   114 came from nonzero vertical deltas (22 positive, 92 negative). `_int_delta` rounds
   fractions to zero. The harness currently turns zero into a downward wheel tick, and ignores
   horizontal scroll. Define common scroll units/direction, preserve nonzero direction,
   reject genuine no-ops, and test data conversion against execution before regenerating.
   Pointer modifier holds also cannot be expressed by the current click/drag schema; key
   modifiers alone do not implement Ctrl-drag. The affected gesture frequency is not measured.

5. **Evaluation and deployment coverage are weak.** Five apps have no validation targets:
   D5, Revit Architecture, Revit Structure, STAAD, and V-Ray. Three of those have no test
   targets either. SketchUp has just 109 validation targets in the initial snapshot.
   The full configuration evaluates only 48 windows, scoring the final action with recorded
   history; that is not successful end-to-end CAD construction. Neither FreeCAD nor OpenSCAD
   appears in this dataset, though those are the current demo apps. Add application/project-
   stratified evaluation where enough independent projects exist, and a separate deployment-
   app dataset plus native-file/geometry task tests. Tiny apps cannot support credible splits
   by dividing frames from the same workflow.

6. **“Full epoch” does not mean every training window is seen.** Weighted sampling uses
   replacement with 5,533 draws for the 5,533 train windows in the isolated view. From the
   actual weights, expected unique coverage is about 3,179 windows (57.46%) per epoch.
   This is a sampling-design tradeoff, not a loader failure. If full coverage is required,
   use a coverage-preserving pass, with explicit supplemental balancing/oversampling.

7. **Heuristic quality scores overstate confidence.** All 287,374 initial rows score 1.0,
   yet visual review found retained Windows quick-settings interaction in Revit Architecture
   row `02c8d1d0-3c5e-4fd4-8617-56acf00fc54f--00011`. A lexical scan flags 20 possible
   system/recording-related intents, including false positives such as modeling a volume;
   these were not automatically removed. 4,331 original observations are less than one source
   frame before their first action, risking same-frame/post-action visibility. Neither issue
   is caught by the present score. Add foreground-app checks, source-frame timestamp margins,
   outcome/annotation spot checks and explicit quarantine reasons.

## Visual and semantic limitations

Ten train screenshots were manually inspected, one per app, using the second of the first
three sampled rows per app. Controls/text were readable and target positions were generally
plausible (e.g. AutoCAD units, Revit New Project, V-Ray color temperature). One contained
off-task Windows settings. This is a small, early-workflow-biased sanity check, not a measured
corpus-wide error rate. Full source replay covered one retained workflow. No exhaustive OCR
privacy scan, perceptual near-duplicate search, final CAD artifact verification, or legal rights
review was performed. Those remain release checks; prior handoff flags dataset licensing as
unresolved. Remaining unbuilt workflows must be audited when available.

## Reproduction and next gate

Run from the repository root; outputs stay outside production training views:

```bash
harness/.venv/bin/python scripts/audit_corpus_quality.py --output runs/corpus-audit-2026-09-08
PYTHONPATH=src .venv/bin/python scripts/audit_corpus_alignment.py --output runs/corpus-audit-2026-09-08
PYTHONPATH=src .venv/bin/python scripts/audit_corpus_alignment.py --output runs/corpus-audit-2026-09-08 --frames
/home/armand0e/Documents/training/.venv/bin/python training/token_report.py --config training/configs/full64k.yaml --policy-dir runs/corpus-audit-2026-09-08/trajectory --splits train.jsonl validation.jsonl --output runs/corpus-audit-2026-09-08/tokens.json
/home/armand0e/Documents/training/.venv/bin/python training/audit_training_view.py --config training/configs/full64k.yaml --view runs/corpus-audit-2026-09-08/trajectory --output runs/corpus-audit-2026-09-08
```

Recommended next work: approve and implement a versioned corpus repair/rebuild, then rerun
these audits and representative task evaluations before full release training. The current
full-run service only gates completeness and structural validation; it does **not** enforce
the semantic findings above. Its success marker must not be treated as a quality certificate.
