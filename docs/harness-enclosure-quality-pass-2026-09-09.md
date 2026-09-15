# Document protection, enclosure tools, and authored seed traces

This is a bounded reliability/capability pass, not a claim that CADPilot is now a
general professional CAD system. Main app 7800 was restarted with explicit user
approval on 2026-09-09 at 21:28:04 UTC. HTTP and model health checks passed, all 18
saved artifact hashes were preserved, and no sessions were open at restart.

## Confirmed fresh-session defect

The affected main session `2d40fb31` had no active FreeCAD document and no agent
modeling actions before the pause. `_desktop_input` marked a click/key as a manual
edit, and `_execute` assigned the same flag to the agent's GUI actions. Native
modeling treated the flag as proof that it could not safely proceed.

The new FreeCAD App document observer records a complete inventory of up to 64
open documents, stable document tokens, and content-change epochs. GUI selection,
visibility and camera changes do not increment content epochs. Baselines are
adopted only for empty setup or explicit saved-recipe acknowledgment/open receipts.
A new nonempty document, changed existing document, active edit, unavailable/stale
inventory, observer restart or truncated inventory remains protected. Background
documents cannot be hidden by an empty active document. A short settling interval
prevents checking an observation from before an input has taken effect.

User input and agent GUI work are separate. The guard checks before generation and
again before commit, and the check participates in steering cancellation. Explicit
branching never closes, writes or merges an existing GUI document. OpenSCAD still
uses conservative input-based protection because it has no equivalent content
observer; this limitation is not silently waived.

Live proof: `runs/harness-checks/edit-guard-gpi7nzq3/result.json` covers a fresh
click, saved-revision opening, camera/selection, actual spreadsheet edits, explicit
branch acknowledgment, and unsaved background geometry. The independent native
observer check also passed in `runs/harness-checks/observer-zmluwfu4`.

## Native geometry and export changes

- `rounded_box`: four vertical rounded corners; length, width, height and corner
  radius remain expression-bound in native FreeCAD features and OpenSCAD. This is
  not an arbitrary edge fillet or a general dress-up feature.
- `parts`: explicit final layout of 2–8 separated finished solids. Each part must
  be connected; touching/overlapping parts fail. Base and removable lid need not
  be fused into an invalid “single solid.” This is not an assembly/joint solver.
- Multi-part status/report/UI no longer say “one valid solid.” Geometry validity
  still does not certify user requirements, fit, cooling or printability.
- Final tessellation starts from a cleaned BREP. Binary STL artifacts are audited
  independently for closed consistently oriented edges, nondegenerate triangles,
  outward exterior shells, native-volume agreement and bounds. Interior cavity
  shells are supported. The audit does not claim an independent triangle
  self-intersection certification. Compiler hashes include the STL auditor.
- Full native recipes have an 8,192-token allowance and a separate bounded
  180-second response timeout. A read timeout no longer restarts the identical
  expensive generation three times. Stop/steering remain interruptible.
- Planner instructions distinguish known device variants/specifications from
  provisional dimensions, functional geometry from decoration, and validity from
  verified fit. Prompt changes alone are not an engineering verification system.

During testing, the bundled FreeCAD Mesh API produced order-dependent
`isSolid`/self-intersection results after BREP operations, while the same imported
mesh checked correctly in a pristine process. The compiler now audits serialized
triangles deterministically; the additional independent seed verifier runs Mesh
diagnostics in a separate process.

## Hand-authored seed data

Source and usage: [training/curated/README.md](../training/curated/README.md).
Completed replay: `runs/curated-cad/replay-i9u1qxg7/manifest.json`.

Five assistant-authored episodes contain seven successful builds and one real
failed cutter call. The correction keeps the existing saved blank plate intact
until a valid new revision is ready. All seven successes have exact FreeCAD/STEP
oracle comparisons, actual spreadsheet edit/save/reopen checks, and rendered
FreeCAD/OpenSCAD STL checks. The enclosure oracle uses kernel edge fillets rather
than the compiler's overlapping box/cylinder construction; exact-shape difference
was zero before and after the chosen parameter edit.

These are internal recipe-generation/recovery demonstrations, not the live
planner decision schema and not visual-action trajectories. They need an explicit
training adapter and CAD expert review. Failed calls are not supervised as correct
targets. Family labels prevent placing the closely related plate episodes in
different splits. No screenshots or successful tool responses were invented.
No on-policy claim is made, and this known regression set is not held-out evaluation.
The actual two-part file was opened and visually inspected through the production
presenter (`runs/harness-checks/curated-preview-xrg9u3_3/enclosure.png`); both base and
vented lid rendered, and the new content guard remained clear after opening.

The generic enclosure is not a Raspberry Pi 3 fit claim. Exact board variant,
connector/keep-out envelopes, supports, fasteners, retention, material allowances,
thermal behavior and physical validation remain separate requirements.

## Training and remaining boundaries

The v3 rebuild was at 217/597 workflows at 20:21 UTC. Training remains explicitly
unauthorized and unstarted. The frozen dataset builder/source files were not edited;
the new seed replay lives outside the rebuild and is quarantined.

Remaining major capabilities include general sketch/dress-up operations, assemblies,
imported-model editing, more robust OpenSCAD document observation, per-requirement
engineering checks, expert-reviewed GUI demonstrations, and fresh on-policy CAD
rollouts. Do not present this pass as proving professional-grade modeling generally.

## Final verification and candid stock-model result

125 automated harness tests pass. Real FreeCAD guard tests pass in
`runs/harness-checks/edit-guard-tgm456_0`; the desktop/mobile browser regression
passes with no page errors. A short real stock-endpoint request accepted the new
kind-specific schema and returned a valid rounded-body recipe.

The full stock-model enclosure regression **failed**. Before the schema change it
emitted missing radius and invalid one-input placement booleans
(`runs/harness-checks/enclosure-live-66ea7ud7/activity.json`). With the tighter schema,
it passed structural validation but tried to emulate relocating a lid using a cut
between disjoint shapes; the native kernel returned measured bounds and a 9.7 mm
separation. It repeated the failed geometry twice, so the duplicate guard prevented
re-execution and no revision was committed
(`runs/harness-checks/enclosure-live-6dj0zpn3/activity.json`, test project
`0cac076c6cf9419d`). This is a negative capability result, not a successful build.
Do not add those stock failures to positive training examples or claim the new
tools make this untrained model professionally reliable. The authored set verifies
that the geometry/tool workflow is feasible, not that the stock model masters it.
