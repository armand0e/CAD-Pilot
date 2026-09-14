"""Facts about a STEP file for grading, run inside the CAD sandbox: python step_report.py z=60 x=10 ...

Writes /work/report.json: solids, bounds, volumes, cylindrical faces (hole/boss radii),
minimum distance between solids and section contours at the requested planes.
"""
import json
import math
import sys

import FreeCAD as App
import Part


def bounds(shape):
    box = shape.BoundBox
    return {'size': [box.XLength, box.YLength, box.ZLength], 'min': [box.XMin, box.YMin, box.ZMin], 'max': [box.XMax, box.YMax, box.ZMax]}


def main():
    shape = Part.Shape()
    shape.read('/work/model.step')
    solids = shape.Solids
    report = {'solids': len(solids), 'volume': shape.Volume, 'area': shape.Area, 'bounds': bounds(shape),
              'per_solid': [{'volume': s.Volume, **bounds(s)} for s in solids], 'cylinders': [], 'sections': {}}
    for face in shape.Faces:
        if isinstance(face.Surface, Part.Cylinder):
            axis = face.Surface.Axis
            report['cylinders'].append({'radius': round(face.Surface.Radius, 3), 'axis': [round(axis.x, 3), round(axis.y, 3), round(axis.z, 3)],
                                        'center': [round(v, 3) for v in face.CenterOfMass], 'area': round(face.Area, 3)})
    if len(solids) >= 2:
        distances = []
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                gap, _, _ = solids[i].distToShape(solids[j])
                distances.append(round(gap, 4))
        report['min_solid_distance'] = min(distances)
    for arg in sys.argv[1:]:
        axis, value = arg.split('=')
        normal = App.Vector(*[1 if c == axis else 0 for c in 'xyz'])
        wires = shape.slice(normal, float(value))
        contours = []
        for wire in wires:
            entry = {'closed': wire.isClosed(), 'length': round(wire.Length, 4), **bounds(wire)}
            if wire.isClosed():
                try:
                    entry['area'] = round(Part.Face(wire).Area, 4)
                except Exception:
                    entry['area'] = None
            contours.append(entry)
        contours.sort(key=lambda c: -(c.get('area') or 0))
        report['sections'][arg] = contours
    with open('/work/report.json', 'w') as output:
        json.dump(report, output, allow_nan=False)


if __name__ == '__main__':
    main()
