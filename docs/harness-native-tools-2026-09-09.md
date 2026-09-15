# CADPilot hybrid native-tools pass — September 9, 2026

## Outcome

Deployed to the managed workspace at `http://127.0.0.1:7800` after confirming it had no
open sessions. The principal change is architectural: simple precise parts no longer depend
on a long sequence of guessed GUI clicks. The stock planner chooses a bounded native-model
tool for representable parts, while the trained/stock desktop policy remains available for
GUI work and explicit **Visual only** operation.

This is not a claim that the stock visual-action model learned CAD, that SFT transfer works,
or that arbitrary modeling is production-ready. The application remains loopback-only and
single-user. The data-only rebuild and training hold were untouched.

## Implemented

- Data-only parametric feature graphs: boxes, cylinders, cones, spheres, placements,
  rotations, union/difference/intersection. Arithmetic expressions are parsed with an AST
  allowlist, never `eval`. Parameters must actually drive geometry. Graph size, depth,
  expansion, dimensions and identifiers are bounded.
- A trusted FreeCAD compiler generates native Part features and a parameter spreadsheet,
  editable OpenSCAD, STEP and STL. Native checks reject invalid/empty/disconnected solids
  and cuts that remove no material. Reports expose actual bounds, volume and per-cut removal.
- Isolated native builds: no host home, network or display; read-only runtime; only an owned
  staging directory is writable. Resource limits, output limits and free-disk floor apply.
- Append-only project revisions with scoped, integrity-checked artifact downloads, compiler
  fingerprints, optimistic-head checks and a short locked commit. Restore creates another
  revision. Generation/compilation can be cancelled before committing; failed attempts leave
  the head unchanged and return their exact error to the model, up to three attempts.
- Working copies, not revision artifacts, are opened in CAD. Existing documents are not
  closed. Native edits cannot silently absorb unsaved GUI changes: viewport input activates
  a conservative guard; **Use saved revision** explicitly selects the saved recipe as base.
- A Model panel with dimensions, solid validity, export links, tool selection and revision
  history. Saved parts and bounded conversation context can be reopened after a session or
  server restart. Unsaved GUI changes and the former session's full event stream are not restored.
- Fixed ASCII policy typing: CAD letters, Space and numeric viewport input now use actual
  keystrokes. Explicit paste and multiline/Unicode editor text retain the clipboard transport
  and previous-text clipboard preservation.

## Evidence

`harness/.venv/bin/python -m unittest discover -s harness/tests -q`: **84 passed**.
Coverage includes arithmetic/code injection, graph validation, unused parameters, real kernel
rotations/cuts, invalid joins, revision integrity/traversal/symlinks, stale commits, restore,
cross-origin control rejection, error feedback, stop/steering races, staging cleanup, manual
work protection and typing-vs-paste behavior, plus the prior lifecycle/supervision tests.

Actual stock model: `qwen3.8-27b` at the local endpoint. Requests were natural language;
the model was not given a plate/spacer/bracket template. Separate reference-shape graders
compared saved artifacts, not model assertions or screenshots.

| Trial | Independent result | Evidence directory under `runs/harness-checks/` |
|---|---|---|
| FreeCAD 60 × 40 × 8 plate, four Ø5 holes, 8 mm offsets | Exact reference match; symmetric difference 0 mm³ | `projects-freecad-9rubfke3/grade-0/` |
| FreeCAD follow-up: 80 mm length, Ø6 holes, other dimensions unchanged | Exact reference match; 0 mm³ | `projects-freecad-9rubfke3/grade-1/` |
| OpenSCAD same initial plate | Passed mesh-tolerant reference check; 0.2622 mm³ difference | `projects-openscad-wx4kzv9d/grade-0/` |
| OpenSCAD same follow-up edit | Passed mesh-tolerant reference check; 0.3665 mm³ difference | `projects-openscad-wx4kzv9d/grade-1/` |
| FreeCAD Ø20/Ø10 × 15 hollow spacer | Exact reference match; 0 mm³ | `native-variety-rt_sl7l6/spacer/` |
| FreeCAD joined L-bracket with through holes along X and Z | Exact reference match; 0 mm³ | `native-variety-rt_sl7l6/bracket/` |

The plate integration test additionally restores r0001 as r0003, closes/reopens the project,
reloads the browser and checks mobile overflow. The earlier first FreeCAD trial completed
the initial plate in approximately 31 seconds (`projects-freecad-e5knvvle/`).

`browser_check.py`: desktop/mobile, real CAD stream, manual input, replay, reconnect and
same-page controls passed with no browser errors. `live_input_check.py`: exact Unicode/
multiline content and prior clipboard preservation passed (`input-im2jmtp0/`).

Image inspection caught integration defects that export checks alone missed: OpenSCAD's
welcome screen did not implement the normal Open shortcut, Ctrl+4 selected Top rather than
fit-to-view, and FreeCAD's first-start page could cover a reopened document. Native sessions
now start in an owned blank OpenSCAD editor / private FreeCAD project preferences. File-open
acknowledgments are checked, and diagonal/fit views are applied. Final inspected screenshots:
`freecad-reopen-final.png`, `openscad-reopen-final.png`.

A final standalone-export check found that console-only FCStd files lacked saved visibility
metadata and could reopen hidden without the harness adapter. The compiler now includes a
minimal native `GuiDocument.xml` with only the final result visible and a fitted top camera.
The headless geometry/meshing path remains unchanged. Rebuilt a stock-generated plate and
opened its copied FCStd directly in a fresh FreeCAD process, with no open-revision mailbox:
only `final_plate` was visible, and the part displayed correctly. Evidence:
`native-export-t33i51zv/standalone.png`. The kernel regression now checks this metadata too;
the final full regression run remains 84/84 passing.

## Boundaries and follow-up work

The native tool is CSG, not a general sketch/constraint solver. It does not currently offer
native fillets, assemblies, imported mesh editing, arbitrary code, or round-trip ingestion of
manual CAD edits. Runtime native validation proves a valid solid and measurements, not that
every semantic requirement was met. The independent reference graders above are test-only.
The UI deliberately says a saved revision is ready for review rather than certifying the task.

Older GUI-only full-task failures remain documented in the September 8 report. Keep the
`live_native_task.py` benchmark in visual-only mode so hybrid-tool success does not hide
desktop-policy regressions. Expanding native tools should come with both malformed-input
tests and independent shape-reference tests, especially for topology-sensitive operations.

Test projects are isolated at `runs/harness-checks/native-projects/`; the service on 7802 is
`cadpilot-harness-native-check` with `CADPILOT_PROJECT_ROOT` set to that directory. Main user
projects use `harness/projects/`. Test artifacts were retained, not placed in the main launcher.

## API/source references

Native implementation follows the installed FreeCAD runtime and its
[expression documentation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Expressions.md).
Fixed OpenSCAD view shortcuts were checked against the exact version's
[MainWindow.ui](https://github.com/openscad/openscad/blob/openscad-2021.01/src/MainWindow.ui).
Private FreeCAD startup preferences follow its
[StartView implementation](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Start/Gui/StartView.cpp).
