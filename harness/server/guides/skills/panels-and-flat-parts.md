# Panels and flat parts

Anything that is essentially a flat profile given thickness: faceplates, gaskets,
brackets from sheet, cams, washers, stencils, PCB outlines, keychain tags. These are
the most reliable models because they are one extruded outline.

## Outline first, then extrude

Build the 2D outline with the cad_paths generators or an SVG path, put the holes in
with with_holes, and extrude once.

```python
from cad_paths import extrude, with_holes, rect, circle, slot, hexagon, polygon, d_shape
faceplate = extrude(with_holes(
                rect(120, 80, 6),                 # rounded-corner plate
                circle(20, (30, 40)),              # a 20 mm round cutout (circle() is DIAMETER)
                slot(30, 8, (80, 40)),             # a slot
                circle(3.4, (10, 10)),             # M3 clearance hole (3.4 mm)
                circle(3.4, (110, 70))),
            height=3)
parts = {"Faceplate": faceplate}
```

## Hand-drawn outlines

For a shape the generators do not cover, write an SVG path (the d attribute only) and
ALWAYS run path_preview before building: it returns bounds, area, which subpaths are
holes, and warns about self-intersection or an unclosed outline. Fix warnings before
extruding.

```python
from cad_paths import extrude
cam = extrude("M0 0 L40 0 C50 0 55 10 50 20 Q40 35 20 25 L0 20 Z", height=6)
```

One path unit is 1 mm. Even-odd nesting makes inner subpaths holes. Use flip_y=True if
you drew it in an SVG editor (screen y is downward; CAD y is up).

## Openings, engraving and edges

- A window/port that goes through: include it as a hole in with_holes, or cut_through
  a separate outline on the chosen plane.
- Engrave/emboss text or a logo: see text-and-labels.md.
- Chamfer or fillet the outer edge for feel: shape.makeChamfer / makeFillet on the
  perimeter edges; inspect the result since edge selection is fiddly.

## Checklist

- The outline previews clean (path_preview: closed, no self-intersection, holes right).
- Holes and slots are sized for their fasteners/parts.
- Thickness is a named parameter; bounds_mm are the real millimetres.
- flip_y is set correctly if the path came from an SVG editor.
