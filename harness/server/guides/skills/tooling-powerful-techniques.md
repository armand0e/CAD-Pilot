# Powerful use of the tooling

The harness gives you more than makeBox. Using these well is the difference between a
crude approximation and a precise, verified part. Read this to work at full power.

## cad_paths: outlines as a first-class language

Import the generators and combinators and compose them:

    from cad_paths import extrude, revolve, loft, pipe, cut_through, gear, rack, \
        rect, circle, slot, polygon, hexagon, d_shape, with_holes

- Generators return correct 2D outlines: rect(w,h,corner_r), circle(diameter,(cx,cy)),
  slot(len,w,(cx,cy)), polygon(...), hexagon(across_flats), d_shape(...), and true
  involute gear(module,teeth) / rack(module,teeth). Note circle and hexagon take a
  DIAMETER / across-flats size, not a radius - a common slip that halves your holes.
- with_holes(outer, *holes) punches holes as part of the profile - always clean and
  through. Prefer it to cutting cylinders one by one.
- extrude(outline, height) turns any outline into a solid. Planes: xy->+z, xz->-y,
  yz->+x, so you can extrude a profile in any direction.
- cut_through(solid, outline, plane, at, depth?) drops an outline onto a plane through
  `at` and cuts along the normal - the clean way to make a side window, slot or port
  without hand-computing a 3D box.
- revolve / loft / pipe cover rotational, transitioning and swept shapes
  (see revolves-lofts-and-sweeps.md).
- Arbitrary shapes: write an SVG path (the d attribute) and run path_preview FIRST -
  it returns bounds, area, which subpaths are holes, and warns on self-intersection or
  an open outline, with a millimetre-grid picture. Never extrude an unpreviewed path.
- create_path_body drafts a reusable profile module (profiles/<name>.py) plus an SVG
  that OpenSCAD can import() - good for a shape you reuse across parts.

## cad_inspect: measure, do not assume

- List objects and faces (paged); face/edge indices belong to that revision and change
  after a rebuild, so inspect again before reusing an index.
- Measure two references (Case:Face3 and Lid:Face1): returns minimum distance and
  intersection volume. Zero distance can mean touching OR intersecting - read the
  intersection volume to tell them apart.
- Section a body with a plane to read internal dimensions and wall thicknesses off the
  contour - the only reliable way to check a wall you cannot see from outside.

Use inspect to VERIFY the numbers you care about, then record a measurement check in
design-spec.json with a JSON pointer into the inspect result (see CAD_GUIDE.md). This
measure-then-record loop is what separates a claimed fit from a proven one.

## Choose the saved views for your object

The four default saved views (iso/top/front/right) are not always the ones that show
your object. In model.py set views = {"name": [dx, dy, dz], ...} (up to 8) to choose
the saved render angles: each value is a camera direction (object -> camera, as
cad_render). For an aircraft, save a nose three-quarter, a planform (top) and a side
profile; for a mug, a three-quarter and a straight-on. These become the saved views
(cad:rNNNN:name) shown in the app and reviewed each turn - aim them at what matters.

```python
parts = {"Plane": plane}
views = {"nose": [1, -0.6, 0.35], "planform": [0, 0, 1], "profile": [0, -1, 0], "tail": [-1, -0.5, 0.3]}
```

## cad_render: see exactly what you need

- Named views (iso/top/front/back/left/right) or a custom camera direction (from
  object toward camera) for any angle. This is ad-hoc inspection; `views` above sets
  the SAVED set that persists and is reviewed.
- bodies=[...] isolates parts; highlight a body or Body:FaceN to point at a feature.
- section={axis:"z",at:10,keep:"below"} renders a cross-section - picture only, the
  saved geometry is untouched - to inspect internal fit visually.
- Returned image ids reopen through view_image even after compaction; crop to zoom
  into detail at full resolution.

## Import exact geometry

import_reference downloads STEP/STL/DXF/SVG/IGES into /work/references/. Then in
python: Part.Shape().read(path) for STEP/IGES, Mesh.Mesh(path) for STL,
importDXF.insert(path, doc.Name) for DXF (circle edges give exact hole centres and
radii), importSVG.insert(path, doc.Name) for SVG. Importing a real mating part or a
DXF hole pattern removes guesswork entirely.

## research and images

- research opens web pages and PDFs to read printed dimensions and standards - the
  authoritative way to size a part to a real device.
- Attached and research images are readable at /work/images at full resolution for
  pixel measurement scaled from a known printed dimension (an estimate, not a source).

## bash sandbox as a workbench

python (also python3) is the bundled FreeCAD Python with numpy and PIL, /work
writable, no network. Use it to script a parametric study, batch-check a family of
sizes, compute a profile, or validate geometry BEFORE cad_build. Always import
FreeCAD before Part (importing Part alone segfaults). cad_build is the operation that
validates, saves and renders a revision; bash is for exploration and scripting.

## FreeCAD vs OpenSCAD

- FreeCAD Python: analytic curves, true STEP output, and face/edge inspection. Default
  choice, and required for detailed measurement.
- OpenSCAD: fast algorithmic CSG; cad_paths.scad mirrors the 2D modules (rounded_rect,
  slot, polygon_n, hexagon, d_shape, svg_outline(file), loft2). Its STEP is faceted,
  not analytic, so prefer FreeCAD when curves or measurement matter.

## Checklist

- Complex profiles come from generators/paths (previewed), not stacked primitives.
- Internal and mating dimensions are measured with cad_inspect (section for hidden walls).
- You rendered the exact view/section that would reveal a problem.
- Real mating geometry was imported when a file existed.
- Claimed dimensions are recorded as measurement checks, not assertions.
