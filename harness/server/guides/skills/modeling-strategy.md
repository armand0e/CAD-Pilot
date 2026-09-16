# Modelling strategy: how an expert approaches any part

This is the general craft that sits under every task skill. A professional does not
start typing booleans; they decompose, parameterise, choose the right construction
method, and build in an order that stays robust. Read this for any non-trivial model.

## 1. Decompose before you build

Break the object into a few primitives and features, and name them. Identify:

- The main mass (the block, cylinder, capsule or extruded profile everything hangs off).
- Additive features (bosses, ribs, lugs, tabs) that fuse onto it.
- Subtractive features (holes, pockets, windows, channels) that cut into it.
- Symmetry: most parts are mirror-symmetric about one or two planes. Build one side,
  mirror it. Repeated features become a loop or a helper function.

Choose an origin that makes the arithmetic easy: a natural centre, a corner at (0,0,0),
or the mounting face at z=0. Good origin choice removes most of the coordinate errors.

## 2. Parameterise everything that matters

Put every meaningful dimension in a named variable at the top, and DERIVE dependent
values rather than repeating numbers. One source of truth makes iteration a one-line
change and keeps features aligned when a dimension moves.

```python
W, D, H, wall = 80.0, 60.0, 30.0, 2.4
inner_w, inner_d = W - 2*wall, D - 2*wall     # derived, never hand-copied
boss_r, boss_h = 4.0, H - wall
```

## 3. Pick the right construction method per feature

- Constant cross-section (plates, brackets, extrusions, most parts): draw the 2D
  outline and extrude it (cad_paths extrude + with_holes). This is the most reliable
  method - prefer it over stacking many primitives when a face is at all complex.
- Rotational (vases, knobs, wheels, nozzles-of-revolution): revolve a profile.
- Transitions and tapers (ducts, round-to-rect, bells): loft between sections.
- Tubes and rails along a curve: pipe / sweep.
- Genuinely blocky solids: primitives (box, cylinder, sphere, cone, torus) + booleans.
- Organic or rounded forms (people, animals, mascots, creatures): compose smooth
  primitives (spheres, capsules, lofted masses) into the right proportions and fuse
  them; keep the silhouette readable rather than chasing fine detail.

Mixing is normal: an extruded body with a revolved knob fused on. Choose per feature,
not per model.

## 4. Build in a robust order

1. Build the main mass.
2. FUSE additive features, each overlapping the mass by 0.5-1 mm.
3. CUT subtractive features, each cutter extending beyond the surfaces it pierces.
4. Fillet and chamfer LAST, only the edges that matter.

Fillets and chamfers early make later booleans fragile and edge indices unstable. Do
them at the end and inspect the result.

## 5. Design for reliable booleans

- Overlap unions and over-run cuts. A cutter that ends exactly on a face leaves a
  zero-thickness sliver and a non-manifold mesh; make it poke through.
- Avoid coincident/coplanar faces between two solids you fuse - they produce fragile
  seams. Offset by a fraction so one clearly enters the other.
- After fuses/cuts that leave seam lines on a flat face, call shape.removeSplitter()
  to merge coplanar faces back into one.
- If a boolean fails or the audit reports a non-manifold edge or mismatched bounds,
  the fix is almost always more overlap or removing a knife-edge contact - not a
  finer tessellation.

## 6. Symmetry, patterns and reuse

- Mirror: build one arm/lug/hole, then mirror across the plane (Part mirror, or place
  by negating a coordinate). Guarantees symmetry and halves the work.
- Linear and polar arrays: a loop placing copies at a pitch, or rotating copies about
  an axis for bolt circles and spokes.
- Helper functions for any feature you make more than once (a boss, a rib, a mount
  point). It keeps them identical and the code short.

```python
def boss(x, y):
    return Part.makeCylinder(boss_r, boss_h, App.Vector(x, y, wall - 1))  # overlaps floor
body = body.fuse(boss(10, 10)).fuse(boss(W-10, 10)).fuse(boss(10, D-10)).fuse(boss(W-10, D-10))
```

## 7. Iterate cheaply

Change a parameter, rebuild, inspect - do not rewrite the model to move one dimension.
Keep the code legible with comments and stable part names so successive edits compound
cleanly. The source is the authority for the next edit; keep it clean.

## Checklist

- The object is decomposed into a main mass plus named additive/subtractive features.
- Every meaningful dimension is a parameter; dependents are derived, not copied.
- Each feature uses the construction method that suits it.
- Build order is mass -> fuse -> cut -> fillet, with overlaps and over-runs.
- Symmetry and repetition are expressed with mirrors, loops and helpers.
