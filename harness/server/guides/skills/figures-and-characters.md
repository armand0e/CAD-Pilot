# Figures and characters

People, animals, mascots, toys, stylised objects. The goal is a readable silhouette:
someone should name it at a glance. You are not sculpting every detail; you are
composing simple primitives into the right proportions and pose.

## Work from the silhouette and proportions

Before any code, state the defining features and their proportions in words, then in
named parameters. A minion, for example: a tall rounded capsule (clearly taller than
wide, roughly 2:1), one or two big goggle eyes on the FRONT taking a large share of
the upper body, a strap across, short arms, short legs, overalls. If the ratios are
wrong the shape is not recognisable no matter how clean the geometry.

Get the big proportions right first; add small features last.

## Compose primitives, then FUSE into one body

Build the trunk, then attach each feature so it OVERLAPS and fuse everything into a
single solid. Features that merely touch or hover will float (see
connections-and-assembly.md).

```python
import FreeCAD as App
import Part

R = 15.0                    # body radius
BODY_H = 60.0               # straight section -> tall capsule (BODY_H/2R = 2:1)
cyl = Part.makeCylinder(R, BODY_H, App.Vector(0, 0, 0))
top = Part.makeSphere(R, App.Vector(0, 0, BODY_H))
bot = Part.makeSphere(R, App.Vector(0, 0, 0))
body = cyl.fuse(top).fuse(bot)

# Eye on the FRONT (-Y), sunk 1 mm into the face so it fuses (no gap!)
face_y = -R
eye = Part.makeCylinder(7, 3, App.Vector(0, face_y + 1.0, BODY_H*0.7), App.Vector(0, -1, 0))

# Legs at the BASE, pointing down (-Z), overlapping the body
leg = Part.makeCylinder(4.5, 12, App.Vector(-7, 0, 1), App.Vector(0, 0, -1))

figure = body.fuse(eye).fuse(leg)   # ... and the rest, all fused
parts = {"Minion": figure}          # ONE solid
```

## Pose and stance

- Feet or base at the lowest Z so it stands (see orientation-and-scale.md).
- The face and defining features point toward -Y so the front render shows them.
- Symmetric features (two arms, two legs, two eyes) are mirrored across the centre.
- A wide, low base or flat feet keep it stable and printable without supports.

## Recognisability beats detail

Two big eyes placed right sell a minion far more than a perfect strap. Spend effort
on the two or three features a person names the character by. Colour is not available,
so shape must carry the identity: exaggerate the signature feature.

## Checklist

- Proportions match the real thing (state the ratio, e.g. body 2:1) and read in front/iso.
- The defining features are present, large enough, and on the FRONT (-Y).
- Everything is fused into one solid (solid_count 1) with overlaps, nothing floating.
- It stands on its base at the lowest Z.
- You rendered front and iso and it is nameable as the requested character.
