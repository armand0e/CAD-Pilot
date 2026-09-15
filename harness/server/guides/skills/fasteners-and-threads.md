# Fasteners and threads

How parts screw together, printed. The headline rule: do NOT model helical threads.
Everything else here is about giving screws, inserts and nuts a home that actually
works off a printer.

## Never model helical threads

A swept helical thread is a valid solid but does not tessellate to a clean manifold
mesh, so the build audit rejects it at every pitch, profile and tessellation. Do not
try. Instead, for a threaded feature model a plain cylinder / clearance hole and
record the thread spec in design-spec.json (for example "M8x1.25, tapped after
printing" or "for M3 heat-set insert"). Choose one of the printable strategies below.

## Clearance holes (screw passes through)

Bolt a part down by letting the screw slide through and thread into something else.
Clearance diameters (loose fit):

- M3 -> 3.4    M4 -> 4.5    M5 -> 5.5    M6 -> 6.6    M8 -> 9.0
- #4 -> 3.1    #6 -> 3.8    #8 -> 4.4

Add a counterbore (flat pocket for a socket-head cap) or countersink (cone for a flat
head) so the head sits flush: a counterbore is a larger coaxial cylinder cut to head
depth; a countersink is a shallow cone (82-90 degrees).

## Heat-set inserts (best threaded hole for plastic)

A brass insert melts into a pilot hole and gives durable metal threads. Model a
straight pilot hole roughly the insert's nominal size (the melt takes up the
difference); confirm the exact hole from the insert datasheet. Typical pilot holes:

- M3 insert -> ~4.0 mm    M4 -> ~5.6 mm    M5 -> ~6.4 mm

Give the boss enough wall around the hole (>= ~2 mm) so it does not split.

## Nut traps (captive nut)

A hexagonal pocket holds a standard nut so a screw can tighten into it. Cut a hexagon
prism sized across-flats to the nut, plus ~0.2 mm, and a clearance hole through:

```python
from cad_paths import extrude, hexagon, circle, with_holes
# M3 nut: 5.5 mm across flats, ~2.4 mm thick; leave 0.2 mm clearance
trap = extrude(hexagon(5.7), 2.6)          # pocket cutter to subtract from the boss
# subtract trap (as a pocket) and a 3.4 mm clearance hole from the part
```

Across-flats: M3 5.5, M4 7.0, M5 8.0, M6 10.0 mm. Orient the pocket so the nut drops
in from a side or the back, and put it far enough below the surface that the screw
engages several threads.

## Self-tapping into plastic

A screw can cut its own thread in a smaller pilot boss (pilot ~ screw core diameter,
about 0.8 x nominal). Fine for light loads and few assembly cycles; use inserts for
anything reused often.

## Checklist

- No helical thread geometry anywhere; thread specs live in design-spec.json.
- Clearance holes match the screw; heads are counterbored/countersunk if they must sit flush.
- Heat-set pilots and nut traps are sized from the real insert/nut, with enough wall.
- Nut traps are reachable (nut can be inserted) and deep enough to engage threads.
