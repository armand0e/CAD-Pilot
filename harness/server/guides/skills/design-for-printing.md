# Design for printing and manufacture

A geometrically valid part can still be unprintable or weak. These are the rules that
make a model succeed as a physical object. Apply them to anything meant to be made.

## Orientation for strength

FDM prints are layered and weak BETWEEN layers (they peel along Z). Orient the part so
its main load runs along the layers, not across them: a hook or bracket should print so
the pull is in-plane, not lifting one layer off the next. State the intended print
orientation; it drives many of the choices below.

## Overhangs and support

- Faces steeper than ~45 degrees from vertical need support and print rough. Design
  them out: replace a flat overhang with a 45 degree chamfer, or a horizontal hole
  with a teardrop (a circle with a pointed top) so it self-supports.
- Short horizontal gaps bridge fine; long ones sag. Add a chamfer under an overhanging
  boss instead of a flat ledge.
- A large flat lid or plate usually prints best flat side down; a tall thin part may
  need reorienting or a wider base.

## Minimum feature sizes

- Walls: at least two perimeters, ~0.8-1.2 mm minimum; 2-3 mm for structural walls.
- Embossed/engraved detail: >= ~0.4 mm wide and deep to survive (see text-and-labels.md).
- Pins and posts under ~2 mm are fragile; thicken or fillet the base.
- Do not model infill or internal lightening lattices - the slicer fills solid regions.
  Design a consistent wall thickness and let infill do the rest.

## Tolerances and fits (printed, PLA/PETG)

- Slip/clearance fit (lid on a box, peg in a hole, parts that assemble): 0.2-0.4 mm gap
  per side.
- Press/interference fit: 0.0 to -0.1 mm; test-print to confirm.
- Running fit (a shaft that turns): 0.3-0.5 mm.
- Holes print slightly undersize; add ~0.1-0.2 mm to a hole meant to clear a pin, or
  plan to drill/ream to size.
- First-layer "elephant's foot" widens the bottom; a small 0.5 mm chamfer on the bottom
  edge keeps a base dimension accurate.

Tolerances are for gaps between SEPARATE parts. Features of ONE part overlap instead
(see connections-and-assembly.md).

## Stability and adhesion

- Give the part a flat, broad base at the lowest Z; tiny contact patches lift off the
  bed. Add a chamfered foot rather than a knife edge.
- Brims and rafts are slicer settings, not model geometry - do not model them.

## Threaded connections

Do not model helical threads. Use clearance holes, heat-set inserts, nut traps or
self-tapping bosses, and record the thread spec (see fasteners-and-threads.md).

## Process note

Defaults above assume FDM in PLA/PETG. Resin (SLA) holds finer detail and tighter
tolerances but needs drain holes for hollow volumes and support planning. If the user
names a process or material, adjust clearances and minimum features to it and record
that in design-spec.json.

## Checklist

- Print orientation is chosen so load runs along layers; it is stated in the spec.
- Overhangs are chamfered/teardropped; no unsupported flat ledges where avoidable.
- Walls and details clear the minimum sizes; no modelled infill.
- Mating parts have real clearance; holes account for undersize; bottom edge chamfered.
- Threads use inserts/nuts/clearance, never modelled helices.
