"""Trusted grader regression fixtures, never training demonstrations. Bundled FreeCAD Python."""
import json
import sys
from pathlib import Path

import FreeCAD as App
import Part

task = json.load(open(sys.argv[1]))
for case in ("correct", "missing_hole", "wrong_size", "wrong_hole_position"):
    length = task["length"] + (2 if case == "wrong_size" else 0)
    shape = Part.makeBox(length, task["width"], task["height"])
    if task["holes"]:
        for i, (x, y) in enumerate(( (x, y) for x in (8, task["length"] - 8) for y in (8, task["width"] - 8))):
            if case == "missing_hole" and i == 0:
                continue
            if case == "wrong_hole_position" and i == 0:
                x += 2
            shape = shape.cut(Part.makeCylinder(2.5, task["height"], App.Vector(x, y, 0)))
    doc = App.newDocument(case)
    doc.addObject("Part::Feature", "Result").Shape = shape
    doc.recompute()
    doc.saveAs(str(Path(sys.argv[2]) / f"{case}.FCStd"))
    App.closeDocument(doc.Name)
