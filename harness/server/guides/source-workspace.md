# Editable CAD workspace

Pi's read, write, edit and bash tools operate in /work, a persistent project directory.
Read CAD_GUIDE.md and design-spec.json at the start and after compaction. Use inspect
for the current revision, file list, references, user input evidence and specification.

Edit model.py (FreeCAD Python) or model.scad (OpenSCAD), and use cad_build with its
entrypoint. File edits alone do not rebuild or replace the saved model. Multiple
edits can be tested in one build. A failed build preserves both the draft and the
last successful revision. Each successful revision includes its source and spec.
Use cad_checkout to explicitly recover a revision's source; it refuses to overwrite
unsaved file changes unless discard_changes=true. Never discard user edits without
the user's instruction. Existing typed operations remain available until switching
to source with a successful cad_build. They cannot overwrite a source model.

## FreeCAD Python

Use the full FreeCAD/Part APIs, imports, functions, sketches, boolean operations,
patterns and named Python parameters. Millimetres, right-handed x/y/z.

```python
import FreeCAD as App
import Part
length, width, height, wall = 60, 30, 20, 2
base = Part.makeBox(length, width, height)
inside = Part.makeBox(length-2*wall, width-2*wall, height,
                      App.Vector(wall, wall, wall))
case = base.cut(inside)
port = Part.makeBox(12, wall+2, 6, App.Vector(8, -1, 5))
case = case.cut(port)
parts = {"Case": case}
```

Export with parts = {"StableName": shape_or_document_object}; alternatively result
can hold a shape. Name parts meaningfully and keep names stable between builds.
Native output is validated independently in a fresh kernel and exported to FCStd,
STEP, STL and rendered views. FCStd contains named result solids; Python source
retains the construction logic. Source is the authority for subsequent agent edits.
A workspace seeded from an existing model includes base.FCStd and a script that
opens that frozen base. Do not replace the base with each build (edits would compound).

OpenSCAD source is compiled with openscad to STL. The native FCStd/STEP conversion
contains faceted geometry, not analytic curves. The original .scad and mesh are
retained. For analytic STEP and detailed face inspection prefer FreeCAD Python.

bash runs in an isolated sandbox: /work is writable, the CAD runtime is read-only;
there is no network, host home or other project access. python is the bundled
FreeCAD Python. Use research/import_reference for external resources. Imported
models are available under references/. Use bash for scripts or file inspection;
cad_build is the operation that validates, saves and opens a model revision.

## Draw bodies with paths

Use create_path_body to draft a reusable profile module, or directly use cad_paths
in Python. Start with a custom outline instead of a box/cylinder. Supports SVG path
data M/L/H/V/C/S/Q/T/A/Z, absolute and relative commands. Lines and quadratic/cubic
Béziers become native CAD edges; elliptical A arcs use cubic segments of at most
45 degrees (an approximation). All outlines must close with Z. Multiple subpaths
use even/odd nesting for holes. Path data follows https://www.w3.org/TR/SVG/paths.html.
This accepts a path's d string, not an entire SVG/XML document or its transforms.

```python
from cad_paths import extrude, revolve
outline = "M0 0 L40 0 C50 0 55 10 50 20 Q40 35 20 25 L0 20 Z"
body = extrude(outline, height=8)
# Other planes: xy extrudes +z, xz extrudes -y, yz extrudes +x.
# One path unit is 1 mm by default. scale converts units; flip_y=True
# explicitly maps SVG downward-y coordinates to upward CAD coordinates.
parts = {"CurvedHousing": body}
```

For a turned part, revolve a closed radius/height profile in XZ around +Z. Curves,
holes, source parameters, cuts and fillets can be combined freely with the Part API.

## Inspect actual geometry

cad_inspect always names its revision. Query objects, faces (paged), measure two
references such as Case:Face3 and Lid:Face1, or section a body with a plane.
Face/edge indices belong to that revision and can change after a rebuild; inspect
again before reusing them. Measures include minimum distance and intersection
volume, not inferred fit. Zero distance can mean touching or intersecting: examine
the intersection volume. A positive minimum distance is not a complete wall
thickness or mechanical clearance certification.

cad_render supports iso, top, bottom, front, back, left, right or a custom camera
direction (from object toward camera). Isolate bodies with bodies=[...], highlight
a body or Body:FaceN, and clip with section={axis:"z",at:10,keep:"below"}.
Section rendering only changes the picture. It does not cut the saved geometry.
Returned image IDs can be reopened/cropped through view_image after compaction.

## Keep design-spec.json current

Record the objective, coordinate convention, requirements and accepted decisions.
Each requirement has an id, text, origin (user/sourced/assumed), evidence IDs,
status (open/implemented/verified), and features (body names or revision-qualified
face references). Record numeric values with units in text; never promote an
assumption to a sourced measurement. User input IDs are in inspect/spec_read;
source IDs must refer to opened research pages, image IDs to actual saved images.
Reference observations can include crop image IDs, orientation and what is visible.
An image without a scale does not establish precise dimensions.

Use spec_update with expected_version from spec_read and only the fields being
changed. Row arrays upsert by id; omitted rows and fields remain unchanged. For
example, specification={requirements:[{id:"wall",text:"Wall thickness 3 mm",
status:"implemented",evidence:["input:actual-correction-id"]}]} changes only that
requirement. New rows need id/text/origin/evidence. addressed_inputs adds IDs and
each new ID must be linked to a row. open_questions explicitly replaces that list.
To retire a superseded requirement, keep its row with status="retired" and
retirement={reason:"User changed the connector",evidence:["input:actual-id"]}.
Direct Pi edits of design-spec.json still work, with version and deletion checks.

Implemented means the work is done; do not spend turns verifying bookkeeping.
For verified status, add a verification object with one of these scoped checks:
- Measurement: {kind:"measurement",evidence:"measure:actual-id",
  field:"/objects/0/bounds_mm/2",expected:8,tolerance:0.01}. field is a JSON pointer
  into cad_inspect's result and must select a single number, e.g. a section's
  /contours/1/bounds/bounds_mm/0. CADPilot saves the check's evidence link and
  fills missing features from the inspected body. Use actual body/face names for
  explicit feature links. The numeric comparison must actually pass.
- Visual: {kind:"visual",evidence:"measure:actual-render-id",note:"Observed ..."}.
  This records a visual observation, not measured dimensions or mechanical fit.
- Task: {kind:"task",file:"model.py"}. This confirms the file exists and records
  its hash; it does not certify the meaning or correctness of the file contents.

The chosen check must support the stated requirement; matching an unrelated
number does not establish fit. A check verifies its selected value, not every
claim in a paragraph; use separate requirements for independent checks.
Measurement/visual checks become stale after a
rebuild; task checks become stale when their file changes. Legacy verified rows
without a check remain saved but are reported as unverified. Unchanged stale rows
do not block unrelated edits. Updates are archived independently of conversation.

inspect/spec_read return bounded overviews. If a result has context_file, read
that file using Pi read offset/limit for complete records. These regenerable
.cadpilot-context files are excluded from modeling source and dirty checks.
The complete specification remains in design-spec.json. Saved research overview
contains source links and extracted notes, not every full page; reopen a source
with research when needed. User corrections remain in the input evidence ledger.
