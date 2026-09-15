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

Raised or engraved text uses Draft ShapeString (Part has no makeText):

```python
import FreeCAD as App, Part, Draft
doc = App.newDocument()
glyphs = Draft.make_shapestring(String="CAD",
    FontFile="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", Size=10.0)
doc.recompute()
label = glyphs.Shape.extrude(App.Vector(0, 0, 1))   # 1 mm tall; fuse onto a body to raise, cut to engrave
```

TrueType fonts are under /usr/share/fonts/truetype (dejavu, liberation).

OpenSCAD source is compiled with openscad to STL. The native FCStd/STEP conversion
contains faceted geometry, not analytic curves. The original .scad and mesh are
retained. For analytic STEP and detailed face inspection prefer FreeCAD Python.

bash runs in an isolated sandbox: /work is writable, the CAD runtime is read-only;
there is no network, host home or other project access. python (also python3) is the
bundled FreeCAD Python with numpy and PIL. Import FreeCAD before Part in every script
and one-liner (`import FreeCAD, Part`): importing Part on its own segfaults the interpreter. Attached and research images are readable
at /work/images/attachments/<id> and /work/images/research/<id> (full-resolution
originals as <id>.original.png beside them) for pixel measurements scaled from a
printed dimension; printed labels remain the evidence, pixel scaling is an estimate. Read pages and PDFs with research; download STEP/STL/DXF/SVG/IGES
files with import_reference. They appear under references/: Part.Shape().read(path)
opens STEP/IGES, Mesh.Mesh(path) STL, importDXF.insert(path, doc.Name) adds DXF
entities (circle edges give exact hole centres and radii) and importSVG.insert(path,
doc.Name) adds SVG paths. Use bash for scripts or file inspection;
cad_build is the operation that validates, saves and opens a model revision.

## Draw bodies with paths

Custom outlines are SVG path data (the d attribute, not a whole SVG document):
M/L/H/V/C/S/Q/T/A/Z, absolute or relative, several subpaths with even/odd nesting for
holes. One path unit is 1 mm unless scale is given; flip_y=True maps SVG's downward
y to CAD's upward y. Lines and Béziers become native curves; A arcs use cubic
segments of at most 45 degrees. Path data follows https://www.w3.org/TR/SVG/paths.html.

Run path_preview on any hand-written outline before building. It returns bounds,
area, which subpaths are holes, warnings (self-intersections, unclosed outlines) and
a picture with a millimetre grid, the start point and the direction of travel.
Generators return valid paths directly; combine them and check the preview.

```python
from cad_paths import extrude, revolve, loft, pipe, cut_through, rect, circle, slot, polygon, hexagon, d_shape, with_holes
import FreeCAD as App

plate = extrude(with_holes(rect(60, 40, 4), circle(3.4, (8, 8)), circle(3.4, (52, 32)), slot(14, 4, (23, 18))), 3)
housing = extrude("M0 0 L40 0 C50 0 55 10 50 20 Q40 35 20 25 L0 20 Z", height=8)
housing = cut_through(housing, rect(10, 4, 1), plane='xz', at=(15, 0, 2))     # port opening through the front wall
nut = extrude(hexagon(5.5), 2.4)                                                 # M3 nut pocket cutter
knob = revolve("M0 0 L10 0 Q15 10 10 20 L0 20 Z")                               # radius/height profile in XZ, around +Z
vase = loft([rect(40, 30, 6, center=True), circle(24), circle(36)], [0, 40, 70])
handle = pipe("M0 0 C0 30 60 30 60 0", diameter=8, plane='xz')                  # round bar along an open path
parts = {"Plate": plate, "Housing": housing, "Knob": knob}
```

extrude planes: xy -> +z, xz -> -y, yz -> +x. cut_through places the outline on the
plane through `at` and cuts along the normal, completely through unless depth is set.
loft sections are single outlines without holes at increasing heights; pipe sweeps a
circle along an open or closed spine. create_path_body drafts a reusable profile
module (profiles/<name>.py) and an SVG file OpenSCAD can import() with
linear_extrude(). Curves, holes, parameters, cuts and fillets combine freely with
the Part API.

OpenSCAD: `use <cad_paths.scad>;` gives the 2D modules rounded_rect, slot, polygon_n,
hexagon, d_shape, svg_outline(file) (millimetres, y up) and loft2(h0, h1) { a; b; }
(convex hull loft between two 2D children). Combine them with difference() and
linear_extrude/rotate_extrude; pipe has no OpenSCAD counterpart.

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
