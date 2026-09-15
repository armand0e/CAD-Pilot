"""Independent exact-shape oracle for curated seeds. Runs in a CAD sandbox.

Does not import the recipe/compiler. Rounded corners use kernel edge fillets,
not the compiler's box/cylinder construction. Never writes the original revision.
"""
import json
import sys
from pathlib import Path
import FreeCAD as App
import Part
import Mesh


def rounded(length, width, height, radius, x=0, y=0, z=0):
    shape = Part.makeBox(length, width, height)
    vertical = [e for e in shape.Edges if abs(e.BoundBox.ZLength - height) < 1e-7]
    assert len(vertical) == 4
    shape = shape.makeFillet(radius, vertical)
    shape.translate(App.Vector(x, y, z))
    return shape


def expected(kind, edited=False):
    if kind == 'blank-plate':
        return Part.makeBox(70 if edited else 60, 40, 8)
    if kind.startswith('plate'):
        length = (80 if kind == 'plate-resized' else 60) + (10 if edited else 0)
        radius = 3 if kind == 'plate-resized' else 2.5
        shape = Part.makeBox(length, 40, 8)
        for x in (8, length - 8):
            for y in (8, 32):
                shape = shape.cut(Part.makeCylinder(radius, 10, App.Vector(x, y, -1)))
        return shape
    if kind == 'spacer':
        return Part.makeCylinder(11 if edited else 10, 15).cut(Part.makeCylinder(5, 17, App.Vector(0, 0, -1)))
    if kind == 'rounded':
        return rounded(70 if edited else 60, 40, 8, 4)
    if kind == 'enclosure':
        length = 100 if edited else 90
        base = rounded(length, 60, 24, 6).cut(rounded(length - 4, 56, 24, 4, 2, 2, 2))
        base = base.cut(Part.makeBox(16, 4, 8, App.Vector((length - 16) / 2, -1, 6)))
        lid = rounded(length, 60, 2, 6, length + 10)
        lip = rounded(length - 4.6, 55.4, 5, 3.7, length + 12.3, 2.3)
        lip = lip.cut(rounded(length - 8.6, 51.4, 7, 1.7, length + 14.3, 4.3, -1))
        lid = lid.fuse(lip)
        for offset in (-15, -5, 5, 15):
            lid = lid.cut(Part.makeBox(3, 24, 4, App.Vector(length + 10 + length / 2 + offset, 18, -1)))
        return Part.makeCompound([base, lid])
    raise ValueError(kind)


def compare(actual, wanted, tolerance=1e-5):
    error = actual.cut(wanted).Volume + wanted.cut(actual).Volume
    assert actual.isValid() and len(actual.Solids) == len(wanted.Solids), 'Invalid or wrong number of parts'
    assert error < tolerance, f'Geometry differs from independent oracle by {error} mm^3'
    return error


def main():
    task = json.loads(Path('/work/task.json').read_text())
    if '--meshes' in sys.argv:
        # FreeCAD Mesh diagnostics become order-dependent after BREP booleans in
        # this bundled runtime. Inspect imports in a separate pristine process.
        meshes = {}
        for filename in ('model.stl', 'openscad.stl'):
            mesh = Mesh.Mesh('/work/' + filename)
            topology = {'closed': mesh.isSolid(), 'nonmanifold': mesh.hasNonManifolds(), 'self_intersections': mesh.hasSelfIntersections()}
            assert topology == {'closed': True, 'nonmanifold': False, 'self_intersections': False}, f'{filename}: {topology}'
            meshes[filename] = dict(topology, volume_mm3=abs(mesh.Volume))
        volume = expected(task['oracle']).Volume
        for filename, mesh in meshes.items():
            assert abs(mesh['volume_mm3'] - volume) / volume < .003, f'{filename}: volume mismatch'
        Path('/work/mesh-verification.json').write_text(json.dumps(meshes))
        return
    doc = App.openDocument('/work/model.FCStd')
    doc.recompute()
    result = doc.getObject(task['result_object'])
    wanted = expected(task['oracle'])
    error = compare(result.Shape, wanted)
    step = Part.Shape(); step.read('/work/model.step')
    step_error = compare(step, wanted)
    meshes = json.loads(Path('/work/mesh-verification.json').read_text())
    # Real spreadsheet edit, save, close, reopen and recompute, not a regenerated recipe.
    alias, value = 'p_' + task['edit_parameter'], str(task['edit_value'])
    sheet = doc.getObject('Parameters')
    sheet.set(sheet.getCellFromAlias(alias), value)
    doc.recompute()
    edited = result.Shape.copy()
    assert edited.isValid() and len(edited.Solids) == len(wanted.Solids)
    assert abs(edited.Volume - wanted.Volume) > 1, 'Changing the parameter did not change the solid'
    edited_error = compare(edited, expected(task['oracle'], True)) if task['strict_edit_oracle'] else None
    doc.saveAs('/work/edited.FCStd')
    App.closeDocument(doc.Name)
    reopened = App.openDocument('/work/edited.FCStd'); reopened.recompute()
    reopen_error = compare(reopened.getObject(task['result_object']).Shape, edited)
    Path('/work/verification.json').write_text(json.dumps({
        'success': True, 'oracle': task['oracle'], 'native_difference_mm3': error,
        'step_difference_mm3': step_error, 'spreadsheet_edit_difference_mm3': edited_error,
        'reopen_difference_mm3': reopen_error, 'stl_closed': True,
        'parameter_edit': {'name': task['edit_parameter'], 'value': task['edit_value'], 'independent_edited_shape_oracle': task['strict_edit_oracle']},
        'openscad_checked': 'openscad.stl' in meshes, 'mesh_verification': meshes,
        'scope': 'specified seed geometry and one independent parameter edit; not device fit, material or thermal certification',
    }, allow_nan=False))


if __name__ == '__main__':
    main()
