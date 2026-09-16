# Verify your work

A build that passes the geometry audit is not the same as a correct part. The audit
proves the mesh is closed and manifold; it says nothing about whether the thing looks
like what was asked. Most bad results pass the audit. You are the check on meaning.

Do not trust one flattering hero shot - it is how a blank back, an unlit side or an off
profile hides. Look from the angles that would EXPOSE a flaw, then grade the result
honestly against the request on its real axes (silhouette, proportion, whether the
signature features read), name the single weakest one, and fix its
cause before you call it done. The independent `review` sees these same views, so aiming
them at the weak spots - not away from them - is how you make its check, and yours, honest.

## Aim the views at your object's own weak spots

The four default saved views (iso/top/front/right) are generic and often hide the
thing most likely to be wrong: a fan's overflow, a goggle's relief, a scene's layout,
a bracket's back face. Set views = {...} in model.py (see tooling-powerful-techniques.md)
so the SAVED and per-turn-reviewed images look at THIS object's key features and the
angles where its flaws would show. Do this before you decide the model is done - it
makes the review honest instead of flattering.

- A figure or face: a straight-on face view and a low three-quarter, so a flat feature
  (an eye that is really a flat disc) is exposed rather than hidden.
- A scene: a top-down for layout and a low oblique so relief and standing objects read.
- A part that fits: a view with the reference in place, aimed at the mating faces.

## Render the view that would expose a mistake

After a successful build, cad_render the view where the defining feature lives (or read
the saved views you aimed above), then actually read the image and describe what you see.

- A recognisable object: check front and iso, plus your aimed views. Does the silhouette
  read as the thing requested? Would a stranger name it correctly?
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

- You set views to aim the saved/reviewed images at this object's key features and
  weak spots, not left them on the generic defaults.
- You rendered the defining view and read it, not just built successfully.
- You compared against the request and any reference image.
- The parts that should exist exist; nothing extra was exported.
- Dimensions that matter are measured, not assumed.
- If anything looked off, you fixed the cause and re-rendered.
