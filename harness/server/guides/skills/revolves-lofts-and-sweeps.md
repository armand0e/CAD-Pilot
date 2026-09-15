# Revolves, lofts and sweeps

Anything round or tapering: vases, bottles, cups, knobs, bells, nozzles, ducts,
horns, handles, adapters. Three tools cover almost all of it.

## Revolve - rotational symmetry

A revolve spins a profile (in the XZ plane, radius by height) around +Z. Ideal for
anything made on a lathe: vases, bottles, knobs, wheels, feet.

```python
from cad_paths import revolve
# profile is radius (x) vs height (z); it is spun around the Z axis
vase = revolve("M0 0 L25 0 Q30 20 18 45 Q10 65 22 92 L22 96 L0 96 Z")   # solid silhouette
knob = revolve("M0 0 L10 0 Q15 10 10 20 L0 20 Z")
parts = {"Vase": vase}
```

The profile must be a closed outline that includes the axis (an x=0 edge, which becomes
the centre). Draw the OUTER silhouette as one simple loop that never crosses itself:
go up the outside from the base to the rim, then straight back to the axis. A profile
that folds an inner wall back down on itself self-intersects and is rejected.

To hollow a vessel, revolve the solid silhouette, then cut a smaller revolve (the
inside profile) from it, or shell it:

```python
outer = revolve("M0 0 L25 0 Q30 20 18 45 Q10 65 22 92 L22 96 L0 96 Z")
inner = revolve("M0 3 L22 3 Q26 20 15 45 Q7 65 18 92 L0 92 Z")   # smaller, starts above the floor
vessel = outer.cut(inner)                                          # floor stays; open top
```

## Loft - blend between cross-sections

A loft connects outlines stacked at increasing heights. Use it for round-to-square
transitions, tapered bodies, ducts, bells.

```python
from cad_paths import loft, rect, circle
# Round-to-rectangle transition duct (e.g. a fan nozzle)
duct = loft([circle(30), rect(40, 20, 4, center=True)], [0, 50])
# Hollow it by lofting a smaller inner shape and cutting:
bore = loft([circle(26), rect(32, 12, 3, center=True)], [-1, 51])
duct = duct.cut(bore)
parts = {"Duct": duct}
```

Loft sections are single outlines without holes; stack them by height and cut a second
loft for the bore. Keep the number of sections small and their vertices corresponding.

## Sweep / pipe - a profile along a path

pipe sweeps a circle along an open or closed spine - handles, tubes, wires, rails.

```python
from cad_paths import pipe
handle = pipe("M0 0 C0 30 60 30 60 0", diameter=8, plane='xz')   # round bar along a curve
```

For a non-round sweep, use FreeCAD's Part.Wire(...).makePipeShell([profileWire]) or
sweep the profile with makePipe; keep the path smooth (sharp corners can fail).

## Nozzles and ducts that feed an opening

A nozzle transitions the source shape (a round fan) to the target opening (a
rectangular slot). Loft from the source outline to the target outline over the
required length, then cut the matching inner loft for the wall. Keep the target
outline the real size of the opening it feeds (measure it), and give the wall 2-4 mm.

## Checklist

- Revolve profiles are closed and include the axis edge; hollow vessels have a real
  wall loop, not a solid blob.
- Loft sections are single outlines (no holes); the bore is a second loft, cut out.
- Transitions match the real source and target sizes at each end.
- Swept paths are smooth enough to build; verify with cad_render.
