# Enclosures and cases

Boxes that hold something: project cases, battery boxes, junction enclosures, trays.
The reliable recipe is a solid outer shell minus a hollow, plus a lid and openings.

## Core recipe

```python
import FreeCAD as App
import Part

L, W, H, wall = 80.0, 60.0, 30.0, 2.0
outer = Part.makeBox(L, W, H)
inner = Part.makeBox(L - 2*wall, W - 2*wall, H,          # open top: full height inner
                     App.Vector(wall, wall, wall))
case = outer.cut(inner)                                   # a tub with a floor, open top

# A port through one wall (overlap the wall so it cuts cleanly)
port = Part.makeBox(16, wall + 2, 8, App.Vector(20, -1, 10))
case = case.cut(port)
parts = {"Case": case}
```

For the shell in one step, FreeCAD offers shape.makeThickness([faces], -wall, tol),
but the cut-a-smaller-box method above is simpler and predictable. Keep the floor by
starting the inner box at z = wall and giving it full height so only the top opens.

## Lid

Two common styles:

- Lip lid: a plate with a downward rim that sits INSIDE the case opening, sized with
  clearance. Rim outer = inner cavity minus 0.4 mm all round.
- Overlapping lid: a shallow box that caps OVER the outer walls, inner = outer + 0.4.

Model the lid as its own named part, positioned above or beside the case. Give mating
faces 0.3-0.4 mm clearance per side (see connections-and-assembly.md) so it actually
fits when printed.

## Bosses, standoffs and ribs

- Screw bosses: cylinders rising from the floor with a pilot hole, overlapping the
  floor by ~1 mm so they fuse. Pair with clearance holes or heat-set bosses
  (see fasteners-and-threads.md).
- Standoffs to hold a board: cylinders at the board's hole pattern; height sets the
  board above the floor.
- Ribs stiffen a large flat wall: thin tall boxes fused to the inside.

## Details that make it printable

- Fillet sharp outer vertical edges lightly for strength and feel: shape.makeFillet(
  r, [e for e in shape.Edges if ...]) - selecting edges is fiddly, so fillet only when
  it matters and inspect the result.
- Vertical walls print best; large flat lids may need the print flat side down.
- Wall 2-3 mm is a good default for PLA enclosures.

## Checklist

- The cavity has a floor and only the intended faces are open.
- Ports and cutouts overlap the wall they pierce (no zero-width slivers).
- The lid is a separate part with real clearance to the case.
- Bosses/standoffs overlap the floor and sit at the right pattern.
- Wall thickness is a named parameter and reads as intended in cad_inspect.
