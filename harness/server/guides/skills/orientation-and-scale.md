# Orientation and scale

The most common way a model looks wrong is not a broken boolean; it is the right
shape in the wrong pose or the wrong size. Fix orientation and scale first.

## The frame

Millimetres, right-handed, +Z is up. Model with the object standing the way it is
normally seen or printed: its natural "up" along +Z, its base resting on the XY
plane at the lowest Z (z = 0 for the footprint, or symmetric about z = 0 only if
the object is naturally centred there). "Front" faces -Y.

## How the render cameras see it

cad_render (and the saved views) look at the part from these directions. The camera
sits on the named side and looks back at the origin:

- front - camera at -Y, looking +Y. You see the -Y face. Image right is +X, up is +Z.
- back - camera at +Y. You see the +Y face.
- right - camera at +X. You see the +X face. Image right is -Y, up is +Z.
- left - camera at -X.
- top - camera above at +Z, looking down. You see the +Z face. Image up is +Y.
- iso - camera at (1,-1,1): a three-quarter view from the front-right-above.

So put the defining face of the object toward -Y and it will be centred in the
front view and clearly visible in iso. Put feet, base or the printed-down face at
the lowest Z. If the thing that should be at the bottom renders at the top, your
sign is flipped: negate the Z placement, do not rotate the camera to hide it.

## Scale

Decide the real overall size before modelling and give the main dimension a named
parameter. A phone stand is ~150 mm tall; a chess pawn ~45 mm; a wall bracket sized
to its load. After the build, cad_inspect reports bounds_mm - check the three
numbers are the millimetres you intended. A model that is 10x or 0.1x is usually a
unit slip (cm or inches treated as mm) or an SVG path read at the wrong scale.

## A reliable pattern

```python
import FreeCAD as App
import Part

# One source of truth for size and pose.
HEIGHT = 120.0            # real height in mm
up = App.Vector(0, 0, 1)  # +Z
# Build features relative to a base at z = 0 so the part rests on the bed.
base = Part.makeCylinder(20, HEIGHT)            # grows +Z from z=0
knob = Part.makeSphere(14, App.Vector(0, 0, HEIGHT))
part = base.fuse(knob)
parts = {"Part": part}
```

## Checklist

- The base sits at the lowest Z; nothing important is upside down.
- The front of the object faces -Y; render front and it is the face you expect.
- bounds_mm are the real millimetres, all three axes.
- If a recognisable object, its silhouette in the front and side views reads right.
