# Brackets and mounts

Parts that hold or attach a device: fan mounts, PCB mounts, VESA plates, L-brackets,
sensor holders, wall mounts. They are mostly flat plates with a hole pattern and a
few features, so they build reliably from extruded outlines.

## Build the plate from an outline with the holes in one step

```python
from cad_paths import extrude, with_holes, rect, circle, slot
plate = extrude(with_holes(
            rect(80, 60, 4),               # 80x60 plate, 4 mm rounded corners
            circle(5.5, (10, 10)),          # circle() takes DIAMETER: 5.5 mm = M5 clearance
            circle(5.5, (70, 10)),
            circle(5.5, (10, 50)),
            circle(5.5, (70, 50))),
        height=5)
parts = {"Bracket": plate}
```

with_holes cuts the holes as part of the profile, so they are always through and
clean. Prefer this to boolean-cutting cylinders one by one.

## Hole patterns and fastener sizes

Make the pattern a parameter so it can be trimmed to the real device. Common clearance
hole diameters (loose fit over the screw):

- M3 -> 3.4 mm    M4 -> 4.5 mm    M5 -> 5.5 mm    M6 -> 6.6 mm
- #6 -> 3.8 mm    #8 -> 4.4 mm

Standard patterns worth knowing: 120 mm fan = 105 mm square; 140 mm fan = 124.5 mm
square (many use the 120 mm 105 mm pattern too - confirm); VESA = 75 or 100 mm square;
2.5" drive = 61 x 76 mm. Confirm the real pattern from the device or a datasheet
(see from-reference-images.md) rather than trusting memory.

## Flanges, ears and right angles

- Overhanging ears carry the screw holes beyond the body: extend the plate outline,
  or fuse small tabs, and put the holes on them.
- An L-bracket is two plates meeting at 90 degrees; build each and fuse with an
  overlap along the shared edge, or add a fillet/gusset at the inside corner for
  strength.
- A gusset (triangular web) between the two arms greatly stiffens an L-bracket.

## Fitting to a device

Model the device as a reference solid to check that holes line up and nothing
collides - then remove it from parts before the final build
(see connections-and-assembly.md). The exported bracket must not contain the phone,
board, fan or card it mounts.

## Checklist

- Holes are through, correctly sized for the fastener, and on a parameterised pattern.
- The pattern matches the real device (confirmed, not guessed).
- Ears/flanges overlap the body so they are one solid.
- The mounted device is a reference only and is NOT in parts.
- Plate thickness suits the load (3-6 mm typical) and reads right in cad_inspect.
