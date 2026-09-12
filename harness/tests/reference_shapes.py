"""Independent test oracle, not imported by the application or recipe compiler."""
import json
from pathlib import Path
import FreeCAD as App
import Part

kind=json.loads(Path('/work/task.json').read_text())['kind']
if kind=='spacer':
    expected=Part.makeCylinder(10,15).cut(Part.makeCylinder(5,15))
elif kind=='bracket':
    expected=Part.makeBox(60,30,5).fuse(Part.makeBox(5,30,40))
    expected=expected.cut(Part.makeCylinder(3,7,App.Vector(45,15,-1)))
    expected=expected.cut(Part.makeCylinder(3,7,App.Vector(-1,15,25),App.Vector(1,0,0)))
elif kind=='fan':
    expected=Part.makeBox(150,150,3)
    for x in (12.75,137.25):
        for y in (12.75,137.25):
            expected=expected.cut(Part.makeCylinder(2.5,5,App.Vector(x,y,-1)))
    expected=expected.cut(Part.makeCylinder(60,5,App.Vector(75,75,-1)))
else:
    raise ValueError(kind)
doc=App.openDocument('/work/model.FCStd');doc.recompute()
leaves=[o for o in doc.Objects if hasattr(o,'Shape') and not o.Shape.isNull() and not o.InList]
assert len(leaves)==1
actual=leaves[0].Shape
error=actual.cut(expected).Volume+expected.cut(actual).Volume
assert actual.isValid() and len(actual.Solids)==1 and error < 1e-5, error
Path('/work/reference.json').write_text(json.dumps({'success':True,'symmetric_difference_mm3':error,'volume_mm3':actual.Volume}))
