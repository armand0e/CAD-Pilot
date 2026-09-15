# Mechanisms and gears

Parts that transmit motion: spur gears, racks, pulleys, shafts, bearing seats, cams.
The cad_paths library gives you correct involute gears directly, so use it rather
than hand-drawing teeth.

## Gears and racks

```python
from cad_paths import extrude, with_holes, gear, rack, circle
# A 20-tooth module-2 spur gear, 6 mm thick, 6 mm bore (circle() is DIAMETER)
pinion = extrude(with_holes(gear(module=2, teeth=20), circle(6, (0, 0))), 6)
# A matching rack (same module) for rack-and-pinion
bar = extrude(rack(module=2, teeth=12), 6)
parts = {"Pinion": pinion, "Rack": bar}
```

Key gear facts:
- Module (mm) must match between meshing gears. Pitch diameter = module x teeth.
- Centre distance between two meshing gears = module x (teeth_a + teeth_b) / 2.
- gear(module, teeth, pressure_angle=20) - 20 degrees is standard.
- Add a bore with with_holes(gear(...), circle(bore_r, (0,0))). Add a hub by fusing
  a short cylinder around the bore.

Do not draw involute teeth by hand; the generator is correct and meshes cleanly.

## Shafts, bores and bearing seats

- A shaft is a cylinder; a bore is a circle hole in the profile or a cut cylinder.
- Printed rotating fit: bore ~0.2-0.4 mm larger than the shaft. Confirm from the real
  shaft diameter.
- Bearing seat: a counterbored pocket sized to the bearing outer diameter with a
  small shoulder to seat against. Common bearing 608: OD 22, ID 8, width 7 mm.
- A D-shaped bore (cad_paths d_shape) keys a part to a flatted motor shaft.

## Motion and clearance

Gears and other moving parts are SEPARATE solids that must not overlap in the model,
unlike fused features. Place them at the correct centre distance with running
clearance; if they share material they will not turn. Verify centre distance with
cad_inspect.

## Checklist

- Meshing gears share a module; centre distance = module x (Ta+Tb)/2, verified.
- Bores and seats are sized for a real running/press fit, not zero clearance.
- Moving parts are separate, non-overlapping solids at the right spacing.
- Teeth come from gear()/rack(), not hand-drawn.
