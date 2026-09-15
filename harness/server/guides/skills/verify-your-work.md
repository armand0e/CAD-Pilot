# Verify your work

A build that passes the geometry audit is not the same as a correct part. The audit
proves the mesh is closed and manifold; it says nothing about whether the thing looks
like what was asked. Most bad results pass the audit. You are the check on meaning.

## Render the view that would expose a mistake

After a successful build, cad_render the view where the defining feature lives, then
actually read the image and describe what you see.

- A recognisable object: render front and iso. Does the silhouette read as the thing
  requested? Would a stranger name it correctly?
- A part that fits something: render with the reference in place and check the mating
  faces meet with the intended gap.
- A face with a hole pattern: render that face straight on (front/back/left/right/
  top) and count the holes and their spacing.

Compare against the request and any reference image. If the user gave a picture, open
it with view_image and put it next to your render in your mind: same proportions?
same orientation? same features present?

## Name the failure, then fix the cause

When the render is wrong, say precisely what is wrong before editing: "the handle is
on the wrong side", "the body is too squat, real ratio is 2:1", "the eye is floating".
Then change the cause in model.py. Re-rendering the same wrong model, or nudging
numbers blindly, wastes builds.

## Measure the claims you make

For a dimension that matters, verify it rather than assert it. cad_inspect measures
distances and bounds; record a measurement check in design-spec.json with the JSON
pointer into the inspect result (see CAD_GUIDE.md). A visual note is an observation,
not a measurement - do not log "fits" as if measured.

## Common "passes audit but wrong" cases

- Floating features that look attached (see connections-and-assembly.md).
- Reference geometry exported as product (see connections-and-assembly.md).
- Upside down or facing away (see orientation-and-scale.md).
- Right shape, wrong size by 10x (unit slip).
- A hollow where a boolean cut went through more than intended.
- A recognisable object whose proportions are off enough that it is not recognisable.

## Checklist

- You rendered the defining view and read it, not just built successfully.
- You compared against the request and any reference image.
- The parts that should exist exist; nothing extra was exported.
- Dimensions that matter are measured, not assumed.
- If anything looked off, you fixed the cause and re-rendered.
