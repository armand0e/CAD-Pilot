# Connections and assembly

Two mistakes make a model that looks fine on screen but is wrong: parts that hover
near each other instead of joining, and reference geometry that gets exported as if
it were the product. This skill prevents both.

## Make parts actually connect

A feature that should be attached must OVERLAP the body it attaches to, not sit
flush and certainly not float with a gap. "On the surface" means the feature crosses
into the body by a fraction of a millimetre, so the union is one solid.

Wrong - the eye starts exactly where the face ends, or 1 mm proud of it, so it is a
separate floating disc:

```python
face_y = -15.0
eye = Part.makeCylinder(6, 2, App.Vector(0, face_y - 1.0, 10), App.Vector(0, -1, 0))  # 1 mm gap!
```

Right - the eye sinks ~1 mm into the face, so it fuses:

```python
face_y = -15.0
eye = Part.makeCylinder(6, 3, App.Vector(0, face_y + 1.0, 10), App.Vector(0, -1, 0))  # overlaps inward
body = body.fuse(eye)
```

Rule of thumb: overlap mating features by 0.5-1 mm. After building, cad_inspect can
measure two bodies; a zero minimum distance with a positive intersection volume
means they truly share material.

## One printable body, or several on purpose

The geometry audit reports solid_count. Decide which you want:

- One object to print: fuse the pieces into a single solid so solid_count is 1.
  parts = {"Toy": body.fuse(arm_l).fuse(arm_r).fuse(leg_l).fuse(leg_r)}
- A real assembly of separate printed pieces (a box and its lid): keep them as
  separate named parts, each its own closed solid, positioned to fit.

Do not leave a single intended object as a pile of disjoint solids that only look
joined because they touch in the render. If it should be one piece, fuse it.

## Reference geometry is NOT output

You will often model something the part must fit - a phone, a PCB, a GPU card, a fan,
a wheel. Model it to check fit, then keep it OUT of the exported parts. Everything in
parts = {...} is written to STL and STEP; a reference solid left in there gets sliced
and printed with the product.

Keep references in their own dict and export only the product:

```python
mount = build_mount()
card  = build_card_reference()   # the GPU you are cooling - context only
fan   = build_fan_reference()    # the fan - context only

# Export only what is manufactured. Reference solids are for your own fit checks.
parts = {"Mount": mount}
# To eyeball the fit, render with the references temporarily, then remove them
# before the final build: parts = {"Mount": mount, "CardRef": card, "FanRef": fan}
```

If you want the fit visible in the saved views, build once with references included
to inspect, then rebuild with parts holding only the manufactured piece(s). The last
successful revision is what the user downloads - make it the clean one.

## Clearance for pieces that move or mate

Real parts need a gap to assemble: a lid over a wall, a peg in a hole, a shaft in a
bearing. Design in clearance, typically 0.2-0.4 mm per side for a printed slip fit,
more for moving parts. This is the opposite of feature overlap - mating faces of
SEPARATE parts need space; features of ONE part need overlap.

## Checklist

- Every feature that should be attached overlaps its parent (no gap, no flush touch).
- solid_count matches intent: 1 for a single object, N for a real N-piece assembly.
- parts = {...} contains only manufactured pieces; references are excluded.
- Separate parts that assemble together have a real clearance gap.
