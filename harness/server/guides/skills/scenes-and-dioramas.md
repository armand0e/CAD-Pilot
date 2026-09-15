# Scenes and dioramas

Several distinct objects arranged together on a base: a pond with rocks and reeds, a
desk with props, a chessboard, a terrarium, a landscape tile. This is different from
modelling one part - the craft is composition, placement and scale, and the output is
deliberately many solids, not one.

## Build a base, then populate it

Start with the ground: a base plate or disc that everything sits on and that sets the
scene's footprint and scale. Water, ground and floor are features CUT INTO or raised
ON the base:

```python
import FreeCAD as App, Part
R, thick = 75.0, 6.0                                   # 150 mm round scene
base = Part.makeCylinder(R, thick)
# Recessed water: a shallow disc cut from the top, leaving a rim of "bank"
water_well = Part.makeCylinder(R - 10, 2.5, App.Vector(0, 0, thick - 2.5))
base = base.cut(water_well)
water = Part.makeCylinder(R - 10, 2.0, App.Vector(0, 0, thick - 2.5))   # a slightly lower water surface
```

Then place each object at a real (x, y) on the base, sitting at the right height
(z = base top, or the water surface for floating things).

## Every object is its own named part; expect many solids

A scene is an assembly. Keep each object a separate named solid so it reads and can be
recoloured or reprinted; solid_count will be > 1 and that is correct here (unlike a
single object, which you fuse). Group repeated elements with a helper and a loop
(see modeling-strategy.md):

```python
def rock(x, y, z, rx, ry, rz):
    s = Part.makeSphere(1.0)                 # unit sphere -> ellipsoid by a scale matrix
    m = App.Matrix(); m.scale(rx, ry, rz)    # Shape.scale() is uniform; use a Matrix for rx!=ry!=rz
    s = s.transformGeometry(m)
    s.translate(App.Vector(x, y, z))
    return s
parts = {"Base": base, "Water": water}
for i, (x, y, s) in enumerate([(40, 30, 8), (-45, 20, 11), (30, -40, 6)]):
    parts[f"Rock{i+1}"] = rock(x, y, thick, s, s*0.8, s*0.7)   # sit on the base top
```

## Placement, scale and a focal point

- Decide the scene's real size first, then size every object to it: a frog ~20 mm on a
  150 mm pond, a lily pad ~25 mm, reeds ~60 mm tall. Objects wildly out of scale break
  the illusion faster than rough shapes do.
- Arrange, do not pile: spread objects around the base, vary their size, and leave open
  space. Put the hero object (the frog) where the eye lands; cluster supporting props
  (reeds, rocks) toward edges.
- Anchor everything to a surface. A lily pad rests ON the water; a frog sits ON a pad;
  reeds root INTO the base. Overlap each object slightly into what it stands on so it
  connects (see connections-and-assembly.md) rather than floating.
- Objects that touch and should join (reeds fused to the base) overlap; objects that are
  simply near each other stay separate.

## Model each object with the right skill

The scene is the arrangement; each object is an ordinary modelling task. A frog is a
figure (figures-and-characters.md), a rock is a scaled/ deformed sphere, reeds are
swept or extruded stalks (revolves-lofts-and-sweeps.md), a bridge is a bracket. Keep
each simple - in a scene, a readable silhouette per object matters more than detail.

## Printing a scene

- One-piece diorama: everything fused/positioned on the base prints together, but tall
  thin parts (reeds) are fragile and overhangs need support - keep them stout.
- Multi-piece: print the base and the delicate props separately and assemble; give
  sockets/pegs a clearance fit (see design-for-printing.md).
- No colour is available, so separation and shape must carry the scene; exaggerate the
  distinct silhouettes.

## Checklist

- A base sets the footprint; water/ground are cut into or raised on it.
- Every object is a named part at a real (x, y), anchored on its surface with a slight
  overlap; nothing floats.
- Objects are to scale with each other and the base; the arrangement has a focal point
  and open space, not a pile.
- solid_count > 1 is expected; the scene is an assembly.
- Set views to a top-down (layout) and a low oblique (relief and standing objects) so
  the saved images actually show the scene reads (see verify-your-work.md).
- Tall/thin elements are stout enough to print, or are separate press-fit pieces.
