# Incremental native workspace and repository cleanup — September 10

## Deployment status

The incremental path is **experimental/test-only**, not a professional CAD capability claim.
Main `cadpilot-harness` / 7800 was **not restarted**. It still runs the September 9 code.
`harness/config.yaml` keeps `native_operations: false` so a later ordinary restart cannot
silently enable this experiment. Test service `cadpilot-harness-native-check` / 7802 has an
explicit `CADPILOT_NATIVE_OPERATIONS=1` environment override in its user-systemd drop-in.
The test project root remains `runs/harness-checks/native-projects`; user projects are untouched.

## Implemented architecture

`server/operations.py` defines data-only typed operations. Each model response contains one
operation, not a complete CSG graph. The host owns IDs, graph edges, boolean wiring, pattern
expansion/chunking, hollowing and lid construction. Existing bounded sandboxed FreeCAD compilation,
STEP/STL checks, append-only revisions and GUI edit guards remain in place.

Tools: create body, hollow, side window, cut/fuse, cut pattern, matching lid, parameter edit,
replace earlier operation, inspect, research, ask, finish. This does not add arbitrary sketches,
surface lofts, general fillets, joints or an assembly solver. The compiler still rebuilds the
bounded native document internally; *model-facing edits and persistence* are incremental.

Every successful mutation stores a hashed `workspace.json` alongside immutable CAD artifacts.
Failed candidates never advance the head. Recovery receives the failed operation, exact diagnostic
and last successful state. Targeted replacement replays dependent operations; restore includes
the ledger. Legacy recipe-only geometry is preserved as a base, with limited semantic tools.
Operation compiler provenance is recorded on new commits and preserved on restore.

Side windows use an explicit face and protect floor/rim/corners. `along="center"` is computed
by the executor. The lid has a complete plate and hollow locating lip, in inverted print layout.
Body-local patterns follow layout translation after edits, preventing misplaced lid-local cuts.
Raw-invalid/geometry-equivalent failed mutations are blocked before another CAD run. Read-only
inspection is idempotent and is not treated as a repeated failed edit. Pre-validation errors do
not create STEP 0 or relabel a previous successful checkpoint as failed.

Finishing requires inspecting the current revision and a separate model requirement review.
Review results are cached per revision. This reviewer is **not deterministic ground truth**.
It cannot certify product fit, airflow, thermal performance, printability or full requirement
coverage. A proper typed, source-grounded mechanical brief and deterministic acceptance checks
are still required before calling the complete harness production-ready.

## Evidence and important negative results

- 143 harness tests pass, including real kernel tests of failed-cut rollback, local repair,
  parameter replay, preserved revisions, restore/reopen, complete lids and wall openings.
- Desktop/mobile/CSP/live-session browser check passes without browser errors.
- Final complete stock-model browser run passes: `runs/harness-checks/enclosure-live-adaqdz3t/`.
  Five successful checkpoints, four rejected/corrected proposals, final review satisfactory,
  native/STEP difference 0.0 mm³, both backend renders and edit/reopen checks passed. This single
  positive case does not resolve the negative-control reviewer failure below. Test sessions closed.
- Stock-generated project `3c4b9e6b4c114d36/r0005` exactly matches the generic enclosure reference:
  native and STEP symmetric difference **0.0 mm³**. FreeCAD and OpenSCAD render; exported meshes,
  an actual spreadsheet edit and save/reopen checks pass. The edited shape oracle is non-strict:
  it proves an editable/reopenable change, not every unstated parametric dependency.
  Evidence: `runs/harness-checks/operation-review-dm9qwbtf/result.json` and `verification/`.
- This is the authored generic enclosure regression prompt, **not a held-out test or Pi 3 fit**.
- Earlier live trial `_oby1fj4` recovered a misplaced cutter but saved the wrong gap/window
  and claimed completion. Its first grading attempt also exposed an asyncio/Playwright test
  runner bug, fixed by running the independent grader in a separate thread/event loop.
- Trial `nh38k4my` exposed a repeat guard blocking inspect/finish after an earlier premature
  finish. Trial `ame5wb0u` produced correct geometry but its reviewer falsely questioned explicit
  local coordinates/inverted lid semantics. Those failures are retained, not counted as passes.
- The corrected reviewer accepts the exact known-good case. However, a negative control with
  an incorrect window and lid gap returned `revise` **for bogus reasons** (claiming X30–60 lies
  outside a 90 mm lid and missing the actual gap discrepancy). The status alone is NOT a passing
  negative-control result. This is why the new path remains test-only.

Next release gate: turn completion findings into typed, auditable comparisons against actual
operation measurements and source/user requirements. A critique must identify a real mismatch,
not merely produce `revise`. Verify true-positive, false-positive and missing-fit cases before
enabling automatic review-driven repair on the main app. Do not train on these review errors.

## Research fixes

Search relevance now matches whole title/snippet tokens and filters each result separately.
`port` no longer matches `support.microsoft.com`, and one relevant result cannot admit unrelated
neighbors. Relevance is not source reliability. Extensionless PDF responses are recognized by
browser MIME type, then fetched through the existing public-only/DNS-pinned bounded reader and
checked for PDF magic before sandboxed text extraction. No paid API, relay or challenge bypass
was added. The user's two reference search files were not edited.

## Storage cleanup and training hold

Executed `scripts/cleanup_obsolete_data.py --execute` with an exact obsolete-directory allowlist,
current-user process file/cwd checks, symlink checks and a verified metadata archive before removal.
Removed 15 old generated-data directories: `data/shards`, processed corpus/policy/trajectory v0–v2,
and their old sample/smoke derivatives. Approximately **46.7 GiB net disk space recovered**.
The before-removal allocated-file estimate was 46.44 GiB; net filesystem space also reflects
directory overhead and concurrent builder/cache activity.

Receipt: `runs/obsolete-data-cleanup-t6iirzrj/cleanup.json`.
Archive: `runs/obsolete-data-cleanup-t6iirzrj/metadata.tar.gz` (1,208 small metadata/report files).
The archive is **not a complete dataset backup**. Removed examples cannot be undone; retained
raw/pinned sources support regenerating derived data, not a promise of byte-identical old v1/v2.

Preserved all raw source caches (the running builder can reuse them), v3 shards/caches, audit/gate
evidence, all checkpoints including checkpoint-225, and all CAD projects. Cleanup did not change
the v3 builder or `src/cad1000` files. Frozen fingerprint/gate check passes; both training holds
remain. At 06:55 UTC: **521/597** workflows, all four workers alive, approximately **142 GiB free**.
Full rebuild, final structural/processor audits and assembly are still pending. Training remains
unauthorized and unstarted. No SFT, pilot, RL, adapter load or training UI was introduced.
All 18 immutable main-project artifact hashes were checked after the work and match their manifests.
