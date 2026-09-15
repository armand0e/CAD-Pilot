# Text and labels

Raised or engraved letters and numbers on a part: nameplates, dials, keycaps, labels,
counters. FreeCAD's Part has no text primitive; use Draft ShapeString.

## The pattern

```python
import FreeCAD as App, Part, Draft
doc = App.newDocument()
glyphs = Draft.make_shapestring(String="CAD",
    FontFile="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", Size=10.0)
doc.recompute()
label = glyphs.Shape.extrude(App.Vector(0, 0, 1.0))   # 1 mm tall text solid
```

- Size is the cap height in mm. Extrude height is how far it stands up or cuts down.
- Fonts live under /usr/share/fonts/truetype (dejavu, liberation). Bold reads better
  when small or printed.
- The text is created at the origin in the XY plane; MOVE and ROTATE it onto the face
  you want before fusing or cutting: label.translate(App.Vector(x, y, z)) and, for a
  vertical face, rotate it upright first.

## Raise or engrave

- Raised: position the text solid so it overlaps the surface by a fraction, then
  body.fuse(label). Raised text needs ~0.6-1.0 mm height to survive printing.
- Engraved: position it just into the surface and body.cut(label). Keep engraving
  ~0.4-0.8 mm deep; too deep weakens a thin wall.

Overlap the surface slightly in both cases so the boolean is clean
(see connections-and-assembly.md).

## Placement on a vertical face

Text is drawn flat (XY). To put it on a front (-Y) wall, rotate it 90 degrees about X
so it stands up, then translate onto the face with a small overlap:

```python
label.rotate(App.Vector(0,0,0), App.Vector(1,0,0), 90)   # stand text upright, facing -Y
label.translate(App.Vector(x_left, -wall + 0.3, z_bottom))
body = body.cut(label)                                     # engrave into the front wall
```

## Checklist

- FreeCAD imported before Part; document recomputed before reading glyphs.Shape.
- Text is legible: bold font, cap height and stroke survive the print.
- It is rotated/placed onto the intended face and overlaps it slightly.
- Raised >= ~0.6 mm; engraved ~0.4-0.8 mm; a thin wall is not cut through.
