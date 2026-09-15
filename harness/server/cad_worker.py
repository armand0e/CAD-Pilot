"""Trusted native compiler, executed in a resource-limited, networkless sandbox.

The input is validated data, never model-generated executable code.
"""
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import math

import FreeCAD as App
import Part
import MeshPart
from design import FONT, expression, validate_design
from stl_audit import audit_stl

MAX_REFERENCE_FACETS = 400000


def shape_summary(shape):
    box = shape.BoundBox
    return {'min_mm': [box.XMin, box.YMin, box.ZMin], 'max_mm': [box.XMax, box.YMax, box.ZMax],
            'volume_mm3': shape.Volume, 'solid_count': len(shape.Solids)}


def material_bands(shape, point, axis):
    """Solid intervals of shape along the axis line through point, in mm."""
    box = shape.BoundBox
    lo, hi = [box.XMin, box.YMin, box.ZMin][axis] - 1, [box.XMax, box.YMax, box.ZMax][axis] + 1
    start, end = App.Vector(point), App.Vector(point)
    setattr(start, 'xyz'[axis], lo)
    setattr(end, 'xyz'[axis], hi)
    try:
        inside = shape.common(Part.makeLine(start, end))
        bands = sorted((min(getattr(v.Point, 'xyz'[axis]) for v in e.Vertexes),
                        max(getattr(v.Point, 'xyz'[axis]) for v in e.Vertexes)) for e in inside.Edges)
    except Exception:
        return []
    merged = []
    for a, b in bands:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in merged]


def explain_missed_cut(base, cutter):
    """One plain sentence: where the cutter is relative to solid material."""
    b, c = base.BoundBox, cutter.BoundBox
    axes = 'xyz'
    def span(box, i):
        return f'{axes[i]} {[box.XMin, box.YMin, box.ZMin][i]:.6g}..{[box.XMax, box.YMax, box.ZMax][i]:.6g}'
    cutter_span = ', '.join(span(c, i) for i in range(3))
    center = c.Center
    outside = [axes[i] for i in range(3) if [c.XMax, c.YMax, c.ZMax][i] < [b.XMin, b.YMin, b.ZMin][i] - 1e-6
               or [c.XMin, c.YMin, c.ZMin][i] > [b.XMax, b.YMax, b.ZMax][i] + 1e-6]
    if outside:
        return (f'The cutter ({cutter_span}) lies completely outside the body ({", ".join(span(b, i) for i in range(3))}); '
                f'it is beyond the body along {", ".join(outside)}.')
    lines = []
    for i in range(3):
        bands = material_bands(base, center, i)
        if bands:
            lines.append(f'along {axes[i]} through the cutter center, solid material is at ' +
                         ', '.join(f'{a:.6g}..{b:.6g}' for a, b in bands))
    return (f'The cutter ({cutter_span}) sits inside an empty region of the body and touches no solid material'
            + ('; ' + '; '.join(lines) if lines else '') + '. Move it into a wall band or extend it through one.')


def vector(values):
    return App.Vector(*values)


def profile_wire(points, plane='xy', z=0.0):
    """Closed polygon wire from numeric 2D points, in the XY plane at z or the XZ plane."""
    vectors = [App.Vector(x, y, z) if plane == 'xy' else App.Vector(x, 0, y) for x, y in points]
    return Part.makePolygon(vectors + vectors[:1])


def placed(shape, position, rotation):
    shape = shape.copy()
    shape.Placement = App.Placement(vector(position), rotation).multiply(shape.Placement)
    return shape


def profile_shape(kind, dims, options, position, rotation):
    """Solids from profiles/paths/sections/text. Evaluated numerically: FreeCAD keeps the
    resulting shape, OpenSCAD keeps the parametric source."""
    if kind == 'extrude':
        shape = Part.Face(profile_wire(options['profile'])).extrude(App.Vector(0, 0, dims[0]))
    elif kind == 'revolve':
        shape = Part.Face(profile_wire(options['profile'], plane='xz')).revolve(App.Vector(0, 0, 0), App.Vector(0, 0, 1), dims[0])
    elif kind == 'pipe':
        path = [vector(p) for p in options['path']]
        wire = Part.makePolygon(path)
        direction = path[1] - path[0]
        direction.normalize()
        circle = Part.Wire(Part.makeCircle(dims[0], path[0], direction))
        try:
            shape = wire.makePipeShell([circle], True, False, 2)  # rounded corners at path bends
            if shape.isNull() or not shape.isValid() or shape.Volume <= 0:
                raise ValueError('pipe shell failed')
        except Exception:
            pieces = [Part.makeSphere(dims[0], path[0])]
            for a, b in zip(path, path[1:]):
                step = b - a
                pieces.append(Part.makeCylinder(dims[0], step.Length, a, step))
                pieces.append(Part.makeSphere(dims[0], b))
            shape = pieces[0].fuse(pieces[1:])  # never removeSplitter: it hangs OCC on these joints
    elif kind == 'loft':
        wires = [profile_wire(section['profile'], z=section['z']) for section in options['sections']]
        shape = Part.makeLoft(wires, True, False)
    elif kind == 'text':
        strings = Part.makeWireString(options['text'], FONT, dims[0], 0)
        solids = []
        for glyph in strings:
            faces = [Part.Face(w) for w in glyph]
            faces.sort(key=lambda f: -f.Area)
            outer = faces[0]
            for inner in faces[1:]:
                outer = outer.cut(inner)
            solids.append(outer.extrude(App.Vector(0, 0, dims[1])))
        if not solids:
            raise ValueError('text produced no glyph geometry')
        shape = solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]
    else:
        raise ValueError(f'unsupported profile kind {kind}')
    if shape.isNull() or shape.Volume <= 0:
        raise ValueError(f'{kind} produced no solid; check the profile winding and dimensions')
    return placed(shape, position, rotation)


def select_edges(shape, rule):
    box = shape.BoundBox
    chosen = []
    for edge in shape.Edges:
        try:
            tangent = edge.tangentAt(edge.FirstParameter)
        except Exception:
            continue
        vertical = abs(tangent.z) > 0.99
        horizontal = abs(tangent.z) < 0.01
        center = edge.BoundBox.Center
        top = abs(edge.BoundBox.ZMax - box.ZMax) < 1e-6 and abs(edge.BoundBox.ZMin - box.ZMax) < 1e-6
        bottom = abs(edge.BoundBox.ZMax - box.ZMin) < 1e-6 and abs(edge.BoundBox.ZMin - box.ZMin) < 1e-6
        on_outer = (abs(center.x - box.XMin) < 1e-6 or abs(center.x - box.XMax) < 1e-6 or
                    abs(center.y - box.YMin) < 1e-6 or abs(center.y - box.YMax) < 1e-6)
        if (rule == 'all' or (rule == 'vertical' and vertical) or (rule == 'horizontal' and horizontal) or
                (rule == 'top' and top) or (rule == 'bottom' and bottom) or (rule == 'outer_vertical' and vertical and on_outer)):
            chosen.append(edge)
    if not chosen:
        raise ValueError(f'no edges match the rule {rule} (top/bottom need straight edges lying exactly at z={box.ZMax:.6g}/{box.ZMin:.6g}); '
                         'try vertical, outer_vertical, horizontal or all, or apply the fillet before fusing curved features on top')
    return chosen


def transformed(kind, base, dims, options, position):
    if kind in ('fillet', 'chamfer'):
        edges = select_edges(base, options['edges'])
        try:
            shape = base.makeFillet(dims[0], edges) if kind == 'fillet' else base.makeChamfer(dims[0], edges)
        except Exception as error:
            shape = None
        if shape is None or shape.isNull() or not shape.isValid() or shape.Volume <= 0:
            raise ValueError(f'{kind} of {dims[0]:.6g} mm on {len(edges)} {options["edges"]} edges failed: the size is too large for '
                             'the adjacent faces or the edges already meet other rounds. Use a smaller size, or apply fillets '
                             'and chamfers to the plain body before adding features next to those edges.')
        return shape
    if kind == 'mirror':
        normal = {'x': App.Vector(1, 0, 0), 'y': App.Vector(0, 1, 0), 'z': App.Vector(0, 0, 1)}[options['plane']]
        return base.mirror(vector(position), normal)
    if kind == 'rotate':
        axis = {'x': App.Vector(1, 0, 0), 'y': App.Vector(0, 1, 0), 'z': App.Vector(0, 0, 1)}[options['axis']]
        shape = base.copy()
        shape.rotate(vector(position), axis, dims[0])
        return shape
    raise ValueError(f'unsupported transform {kind}')


def reference_shape(file_name, position, rotation):
    path = Path('/work/references') / file_name
    if not path.is_file():
        raise ValueError(f'reference file {file_name} is not available in this project')
    if file_name.lower().endswith('.stl'):
        import Mesh
        mesh = Mesh.Mesh(str(path))
        if mesh.CountFacets > MAX_REFERENCE_FACETS:
            raise ValueError(f'reference mesh has {mesh.CountFacets} facets; the limit is {MAX_REFERENCE_FACETS}')
        shape = Part.Shape()
        shape.makeShapeFromMesh(mesh.Topology, 0.05)
        try:
            shape = Part.makeSolid(shape)
        except Exception:
            pass
    else:
        shape = Part.read(str(path))
    if shape.isNull():
        raise ValueError(f'reference {file_name} could not be read')
    return placed(shape, position, rotation)


def face_table(shape, limit=400):
    rows = []
    for index, face in enumerate(shape.Faces[:limit], 1):
        box = face.optimalBoundingBox(False, False)
        try:
            u = (face.ParameterRange[0] + face.ParameterRange[1]) / 2
            v = (face.ParameterRange[2] + face.ParameterRange[3]) / 2
            normal = face.normalAt(u, v)
            normal = [round(normal.x, 3), round(normal.y, 3), round(normal.z, 3)]
        except Exception:
            normal = None
        rows.append({'index': index, 'area_mm2': round(face.Area, 3), 'normal': normal,
                     'min_mm': [round(box.XMin, 3), round(box.YMin, 3), round(box.ZMin, 3)],
                     'max_mm': [round(box.XMax, 3), round(box.YMax, 3), round(box.ZMax, 3)]})
    return rows


DEFAULT_VIEWS = {'iso': (1.0, -1.0, 1.0), 'top': (0.0, 0.0, 1.0), 'front': (0.0, -1.0, 0.0), 'right': (1.0, 0.0, 0.0)}


def render_views(mesh, directory, size=560, views=None):
    """Orthographic shaded views of the exported mesh: painter's algorithm with PIL
    polygon fills (C speed), numpy only for the transforms. No display needed.
    views maps a name to a camera direction [dx,dy,dz] (from the object toward the
    camera, the same convention as cad_render); omitted, the four standard views render."""
    import numpy as np
    from PIL import Image, ImageDraw
    points, facets = mesh.Topology
    if not facets or len(facets) > 400000:
        return []
    vertices = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
    triangles = np.array(facets, dtype=int)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    extent = max(float((vertices.max(axis=0) - vertices.min(axis=0)).max()), 1e-6)
    light = np.array([0.3, -0.5, 0.8]); light /= np.linalg.norm(light)
    p0, p1, p2 = vertices[triangles[:, 0]], vertices[triangles[:, 1]], vertices[triangles[:, 2]]
    normals = np.cross(p1 - p0, p2 - p0)
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 1e-12
    normals[keep] /= lengths[keep][:, None]
    views = views or DEFAULT_VIEWS
    written = []
    for name, direction in views.items():
        forward = np.array(direction, dtype=float)
        if np.linalg.norm(forward) < 1e-9:
            continue
        forward /= np.linalg.norm(forward)
        # Same camera convention as cad_render: up is +Z unless looking near-vertical.
        up = np.array([0.0, 0.0, 1.0]) if abs(forward[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
        right_v = np.cross(up, forward); right_v /= np.linalg.norm(right_v)
        up = np.cross(forward, right_v)
        rotation = np.array([right_v, up, forward])
        cam = (vertices - center) @ rotation.T
        scale = (size * 0.84) / extent
        xy = cam[:, :2] * scale + size / 2
        xy[:, 1] = size - xy[:, 1]  # image rows grow downward
        depth = cam[:, 2]
        order = np.argsort(depth[triangles].mean(axis=1))
        image = Image.new('RGB', (size, size), (250, 250, 250))
        draw = ImageDraw.Draw(image)
        coords = xy[triangles]  # (n, 3, 2)
        for t in order:
            if not keep[t]:
                continue
            tone = 0.35 + 0.6 * abs(float(normals[t] @ light))  # two-sided: mesh winding is not consistent
            color = (int(228 * tone), int(200 * tone), int(160 * tone))
            draw.polygon([tuple(pt) for pt in coords[t]], fill=color)
        image.save(str(Path(directory) / f'view-{name}.png'), optimize=True)
        written.append(name)
    return written


def primitive(doc, name, kind, dimensions, position, rotation):
    """Sources here are compiler-generated arithmetic, never unvalidated user code."""
    obj = doc.addObject({'box': 'Part::Box', 'cylinder': 'Part::Cylinder',
                         'cone': 'Part::Cone', 'sphere': 'Part::Sphere'}[kind], name)
    fields = {'box': ['Length', 'Width', 'Height'], 'cylinder': ['Radius', 'Height'],
              'cone': ['Radius1', 'Radius2', 'Height'], 'sphere': ['Radius']}[kind]
    for field, source in zip(fields, dimensions):
        obj.setExpression(field, source)
    obj.Placement.Rotation = rotation
    for axis, source in zip('xyz', position):
        obj.setExpression('Placement.Base.' + axis, source)
    return obj


def rounded_box(doc, name, dimensions, position, rotation):
    length, width, height, radius = ['(' + d + ')' for d in dimensions]
    def place(x, y):
        # Rotate the local center offsets, keeping every dimension expression-bound.
        vx = rotation.multVec(App.Vector(1, 0, 0))
        vy = rotation.multVec(App.Vector(0, 1, 0))
        return [f'({p}) + ({x}) * {a:.17g} + ({y}) * {b:.17g}' for p, a, b in zip(position, vx, vy)]
    objects = [primitive(doc, name + '_web_x', 'box',
                         [f'{length}-2*{radius}', width, height], place(radius, '0'), rotation),
               primitive(doc, name + '_web_y', 'box',
                         [length, f'{width}-2*{radius}', height], place('0', radius), rotation)]
    for x in (radius, f'{length}-{radius}'):
        for y in (radius, f'{width}-{radius}'):
            objects.append(primitive(doc, name + '_corner', 'cylinder', [radius, height], place(x, y), rotation))
    result = doc.addObject('Part::MultiFuse', name)
    result.Shapes = objects
    result.Refine = True
    return result


def build():
    design = validate_design(json.loads(Path('/work/design.json').read_text()), allow_unused=True)
    params = {p['name']: p['value'] for p in design['parameters']}
    doc = App.newDocument('Model')
    doc.Label = design['name']
    sheet = doc.addObject('Spreadsheet::Sheet', 'Parameters')
    for index, (name, value) in enumerate(params.items(), 1):
        sheet.set(f'A{index}', name)
        sheet.set(f'B{index}', str(value))
        sheet.setAlias(f'B{index}', 'p_' + name)
    doc.recompute()
    objects, cuts, prior_cutters, references = {}, [], {}, {}
    for feature in design['features']:
        kind, name = feature['kind'], feature['id']
        if kind == 'parts':
            parts = [objects[i] for i in feature['inputs']]
            for index, part in enumerate(parts):
                if len(part.Shape.Solids) != 1 or part.Shape.Volume <= 0:
                    raise ValueError(f'{name}: part {feature["inputs"][index]} must be one connected solid')
                for other in parts[:index]:
                    if part.Shape.distToShape(other.Shape)[0] < .001:
                        raise ValueError(f'{name}: parts touch or overlap; use a positive print-layout gap, not fused closures')
            obj = doc.addObject('Part::Compound', name)
            obj.Links = parts
            doc.recompute()
        elif kind in {'union', 'difference', 'intersection'}:
            current = objects[feature['inputs'][0]]
            for index, other in enumerate(feature['inputs'][1:], 1):
                final = index == len(feature['inputs']) - 1
                obj = doc.addObject({'union': 'Part::Fuse', 'difference': 'Part::Cut',
                                     'intersection': 'Part::Common'}[kind], name if final else name + '_stage')
                obj.Base, obj.Tool = current, objects[other]
                doc.recompute()
                if kind == 'difference':
                    removed = current.Shape.Volume - obj.Shape.Volume
                    if removed <= max(1e-7, current.Shape.Volume * 1e-10):
                        cutter = objects[other].Shape
                        diagnostic = {'feature': name, 'cutter': other, 'base': shape_summary(current.Shape),
                                      'tool': shape_summary(cutter), 'minimum_distance_mm': current.Shape.distToShape(cutter)[0],
                                      'intersection_mm3': current.Shape.common(cutter).Volume}
                        raise ValueError(f'{name}: cutter {other} removed no material. ' + explain_missed_cut(current.Shape, cutter)
                            + ' Geometry diagnostic: ' + json.dumps(diagnostic))
                    # Feature cuts that overlap an earlier feature cut (two fan openings
                    # through each other) are reported; hollowing cavities are excluded.
                    entry = {'feature': name, 'cutter': other, 'removed_mm3': removed}
                    chain = prior_cutters.get(current.Name, [])
                    if not other.endswith('_cavity'):
                        # Compare MATERIAL: the share of this cutter's would-be material
                        # that an earlier feature cut had already removed. Passing
                        # through empty cavity space is not an overlap.
                        cutter_shape = objects[other].Shape
                        for prior_name, prior_removed in chain:
                            try:
                                shared = cutter_shape.common(prior_removed).Volume
                            except Exception:
                                continue
                            fraction = shared / max(removed + shared, 1e-9)
                            if fraction > entry.get('overlap_fraction', 0):
                                entry.update(overlaps_cut=prior_name, overlap_fraction=round(fraction, 4))
                        try:
                            chain = chain + [(other, current.Shape.common(cutter_shape))]
                        except Exception:
                            pass
                    prior_cutters[obj.Name] = chain
                    cuts.append(entry)
                else:
                    prior_cutters[obj.Name] = prior_cutters.get(current.Name, [])
                current = obj
        else:
            def source(expr):
                return expression(expr, params, freecad=True)[1].replace('Parameters.', 'Parameters.p_')
            angles = feature['rotation']
            rotation = App.Rotation(App.Vector(0, 0, 1), angles[2]).multiply(
                App.Rotation(App.Vector(0, 1, 0), angles[1])).multiply(App.Rotation(App.Vector(1, 0, 0), angles[0]))
            options = feature.get('options', {})
            numeric = lambda expr: expression(expr, params)[0]
            if kind in ('extrude', 'revolve', 'pipe', 'loft', 'text'):
                resolved = dict(options)
                for key in ('profile', 'path'):
                    if key in resolved:
                        resolved[key] = [[numeric(v) for v in point] for point in resolved[key]]
                if 'sections' in resolved:
                    resolved['sections'] = [{'z': numeric(sec['z']), 'profile': [[numeric(v) for v in pt] for pt in sec['profile']]} for sec in resolved['sections']]
                obj = doc.addObject('Part::Feature', name)
                obj.Shape = profile_shape(kind, [numeric(d) for d in feature['dimensions']], resolved,
                                          [numeric(p) for p in feature['position']], rotation)
            elif kind in ('fillet', 'chamfer', 'mirror', 'rotate'):
                obj = doc.addObject('Part::Feature', name)
                obj.Shape = transformed(kind, objects[feature['inputs'][0]].Shape, [numeric(d) for d in feature['dimensions']], options,
                                        [numeric(p) for p in feature['position']])
            elif kind == 'reference':
                obj = doc.addObject('Part::Feature', name)
                obj.Shape = reference_shape(options['file'], [numeric(p) for p in feature['position']], rotation)
                references[name] = obj
            else:
                dimensions, position = [source(x) for x in feature['dimensions']], [source(x) for x in feature['position']]
                obj = (rounded_box(doc, name, dimensions, position, rotation) if kind == 'rounded_box' else
                       primitive(doc, name, kind, dimensions, position, rotation))
            doc.recompute()
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise ValueError(f'{name}: native kernel produced an empty or invalid shape' +
                             (' (a profile-based solid: check the profile winding and that it does not self-intersect)' if kind in ('extrude', 'revolve', 'pipe', 'loft', 'text') else ''))
        if 'Invalid' in obj.State:
            raise ValueError(f'{name}: recompute failed: {obj.State}')
        objects[name] = obj
    result = objects[design['result']]
    shape = result.Shape
    final = next(f for f in design['features'] if f['id'] == design['result'])
    count = len(final['inputs']) if final['kind'] == 'parts' else 1
    if len(shape.Solids) != count or shape.Volume <= 0:
        raise ValueError(f'Expected one connected solid, got {len(shape.Solids)}. Check overlapping joins and cuts.')
    box = shape.BoundBox
    report = {'valid_geometry': True, 'valid_solid': count == 1, 'solid_count': len(shape.Solids), 'volume_mm3': shape.Volume,
              'bounds_mm': [box.XLength, box.YLength, box.ZLength],
              'min_mm': [box.XMin, box.YMin, box.ZMin], 'max_mm': [box.XMax, box.YMax, box.ZMax],
              'cuts': cuts, 'result_object': result.Name, 'requirements_verified': False,
              'parts': [dict(shape_summary(objects[i].Shape), feature=i) for i in final['inputs']] if count > 1 else [],
              'faces': face_table(shape), 'references': []}
    for name, obj in references.items():
        entry = {'feature': name, 'file': next(f['options']['file'] for f in design['features'] if f['id'] == name), **shape_summary(obj.Shape)}
        for index, part in enumerate(shape.Solids):
            try:
                gap = part.distToShape(obj.Shape)[0]
                overlap = part.common(obj.Shape).Volume
            except Exception:
                gap, overlap = None, None
            entry.setdefault('parts', []).append({'part': index + 1, 'clearance_mm': None if gap is None else round(gap, 3),
                                                 'interference_mm3': None if overlap is None else round(overlap, 3)})
        report['references'].append(entry)
    doc.recompute()
    doc.saveAs('/work/model.FCStd')
    # The console kernel has no ViewProviders. Serialize only the supported visibility
    # properties and a fitted top camera, using FreeCAD's GuiDocument.xml schema. This
    # avoids a GUI renderer mutating cached triangulation during headless STL export.
    gui = ET.Element('Document', SchemaVersion='1', HasExpansion='1')
    ET.SubElement(gui, 'Expand')
    providers = ET.SubElement(gui, 'ViewProviderData', Count=str(len(doc.Objects)))
    for obj in doc.Objects:
        provider = ET.SubElement(providers, 'ViewProvider', name=obj.Name, expanded='0', treeRank='-1')
        properties = ET.SubElement(provider, 'Properties', Count='1', TransientCount='0')
        prop = ET.SubElement(properties, 'Property', name='Visibility', type='App::PropertyBool', status='1')
        ET.SubElement(prop, 'Bool', value='true' if obj == result else 'false')
    extent = max(box.XLength, box.YLength, box.ZLength)
    center = box.Center
    camera = (f'OrthographicCamera {{ viewportMapping ADJUST_CAMERA '
              f'position {center.x} {center.y} {center.z + 2 * extent} orientation 0 0 1 0 '
              f'aspectRatio 1 nearDistance {extent * .01} farDistance {extent * 5} '
              f'focalDistance {extent * 2} height {extent * 1.6} }}')
    ET.SubElement(gui, 'Camera', settings=camera)
    with zipfile.ZipFile('/work/model.FCStd', 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('GuiDocument.xml', ET.tostring(gui, encoding='utf-8', xml_declaration=True))
    Part.export([result], '/work/model.step')
    # Discard tessellation cached by native features/exports; adjacent faces must be
    # tessellated consistently from the final BREP, not intermediate CSG surfaces.
    mesh = MeshPart.meshFromShape(Shape=shape.cleaned(), LinearDeflection=.03, AngularDeflection=.08, Relative=False)
    mesh.write('/work/model.stl')
    try:
        report['views'] = render_views(mesh, '/work')
    except Exception as error:  # A failed picture must never fail a valid build.
        report['views'] = []
        report['view_error'] = str(error)[:200]
    # Mesh.isSolid() in the bundled runtime is order-dependent after BREP booleans.
    # Audit the actual serialized artifact with an independent deterministic reader.
    report['stl_audit'] = audit_stl('/work/model.stl', count, shape.Volume, report['bounds_mm'])
    Path('/work/geometry.json').write_text(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    build()
