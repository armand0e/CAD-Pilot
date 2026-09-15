"""Run only with the bundled FreeCAD Python, inside the private grader sandbox.

The policy cannot read this program, its reference shape, or its result file.
"""
import json
import math
import sys

import FreeCAD as App
import Part


def reference(task):
    shape = Part.makeBox(task["length"], task["width"], task["height"])
    if task["holes"]:
        for x in (task["offset"], task["length"] - task["offset"]):
            for y in (task["offset"], task["width"] - task["offset"]):
                shape = shape.cut(Part.makeCylinder(task["radius"], task["height"], App.Vector(x, y, 0)))
    return shape


def grade(task, path):
    expected = reference(task)
    if task["app"] == "freecad":
        doc = App.openDocument(path)
        doc.recompute()
        bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
        candidates = bodies or [o for o in doc.Objects if hasattr(o, "Shape") and not o.InList]
        shapes = [o.Shape for o in candidates if not o.Shape.isNull()]
        if len(shapes) != 1:
            return {"reward": 0.0, "success": False, "reason": "expected_one_final_shape"}
        actual = shapes[0]
    else:
        import Mesh
        mesh = Mesh.Mesh(path)
        shell = Part.Shape()
        shell.makeShapeFromMesh(mesh.Topology, .01)
        if len(shell.Shells) != 1:
            return {"reward": 0.0, "success": False, "reason": "expected_one_mesh_shell"}
        actual = Part.makeSolid(shell.Shells[0])
    if not actual.isValid() or len(actual.Solids) != 1 or actual.Volume <= 0:
        return {"reward": 0.0, "success": False, "reason": "invalid_solid"}
    bounds = [actual.BoundBox.XMin, actual.BoundBox.YMin, actual.BoundBox.ZMin,
              actual.BoundBox.XMax, actual.BoundBox.YMax, actual.BoundBox.ZMax]
    target_bounds = [0, 0, 0, task["length"], task["width"], task["height"]]
    bbox_error = max(abs(a - b) for a, b in zip(bounds, target_bounds))
    difference = actual.cut(expected).Volume + expected.cut(actual).Volume
    hole_volume = 4 * math.pi * task["radius"] ** 2 * task["height"]
    scale = hole_volume if task["holes"] else expected.Volume
    # Hole-scale tolerance prevents rewarding a blank plate or missing hole just because the
    # main block dominates total volume. Native BRep gets tighter tolerances than triangulation.
    tolerance = max(.01, scale * (.02 if task["app"] == "openscad" else .0001))
    success = bbox_error <= .05 and difference <= tolerance
    reward = 1.0 if success else .2 + .2 * max(0, 1 - bbox_error / 5) + .4 * max(0, 1 - difference / scale)
    return {"reward": min(.8, reward) if not success else 1.0, "success": success,
            "bbox_error_mm": bbox_error, "symmetric_difference_mm3": difference,
            "tolerance_mm3": tolerance, "solid_count": len(actual.Solids), "volume_mm3": actual.Volume}


if __name__ == "__main__":
    try:
        result = grade(json.load(open(sys.argv[1])), sys.argv[2])
    except Exception as error:
        result = {"reward": 0.0, "success": False, "reason": type(error).__name__}
    with open(sys.argv[3], "w") as handle:
        json.dump(result, handle)
