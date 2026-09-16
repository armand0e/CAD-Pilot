"""Trusted geometry validation/inspection, run separately from generated Python."""
import json
import math
from pathlib import Path
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

import FreeCAD as App
import Part
import Mesh
import MeshPart
from cad_worker import face_table, render_views, shape_summary
from stl_audit import audit_stl


def valid(shape):
    if shape.isNull() or not shape.isValid() or not shape.Solids or not math.isfinite(shape.Volume) or shape.Volume <= 0:
        raise ValueError('Output must contain valid, positive-volume closed solids')
    for solid in shape.Solids:
        if not solid.isClosed() or solid.Volume <= 0:
            raise ValueError('Output includes an open or reversed solid')
    if len(shape.Faces) != sum(len(s.Faces) for s in shape.Solids):
        raise ValueError('Output contains loose surfaces alongside solids')


def mesh_for(shape):
    # Lofted and spline surfaces can tessellate to zero-area triangles at seams; the
    # audit tolerates those (they keep the mesh closed and add no volume) and still
    # rejects open or inconsistently oriented meshes.
    return MeshPart.meshFromShape(Shape=shape.cleaned(), LinearDeflection=.03, AngularDeflection=.08, Relative=False)


def detail(shape):
    try:
        b = shape.optimalBoundingBox(False, False)
    except Exception:  # a non-solid visual mesh may not support the optimal box
        b = shape.BoundBox
    return {**shape_summary(shape), 'bounds_mm': [b.XLength, b.YLength, b.ZLength],
            'min_mm': [b.XMin,b.YMin,b.ZMin], 'max_mm': [b.XMax,b.YMax,b.ZMax],
            'area_mm2': shape.Area, 'face_count': len(shape.Faces), 'edge_count': len(shape.Edges)}


def build():
    design = json.loads(Path('design.json').read_text())
    shapes = []
    visual = False
    if design['language'] in ('openscad', 'blender-python'):
        mesh = Mesh.Mesh('source.stl')
        if mesh.CountFacets > 400000:
            raise ValueError('Mesh exceeds 400000 facets; reduce tessellation, subdivision or decimate before export')
        shape = Part.Shape()
        shape.makeShapeFromMesh(mesh.Topology, 1e-6)
        try:
            # Shells can include enclosed voids. Solid construction preserves their orientation.
            outer, inner = [], []
            for shell in shape.Shells:
                solid = Part.makeSolid(shell)
                (outer if solid.Volume > 0 else inner).append(solid)
            built = []
            for index, solid in enumerate(outer):
                for cavity in inner:
                    inverse = cavity.copy()
                    inverse.reverse()
                    if solid.isInside(inverse.CenterOfMass, 1e-6, True):
                        solid = solid.cut(inverse)
                built.append((f'Part{index + 1}', solid))
            if not built:
                raise ValueError('no closed solids')
            for _, candidate in built:
                valid(candidate)
            shapes = built
        except Exception as error:  # noqa: BLE001
            # A Blender mesh that is not watertight still renders and animates: keep it as a
            # visual (non-printable) result instead of failing. OpenSCAD output stays strict.
            if design['language'] != 'blender-python':
                raise ValueError('Output must contain valid, positive-volume closed solids') from error
            visual = True
            shapes = [('Mesh', shape)]
    else:
        for row in json.loads(Path('build-parts.json').read_text()):
            shape = Part.Shape()
            shape.read(row['file'])
            shapes.append((row['name'], shape))
    if not shapes:
        raise ValueError('No solid geometry was generated')
    doc = App.newDocument('CADPilot')
    objects, reports = [], []
    with zipfile.ZipFile('parts.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, shape in shapes:
            if not visual:
                valid(shape)
            obj = doc.addObject('PartDesign::Feature', name)
            obj.Shape = shape
            objects.append(obj)
            part_mesh = mesh_for(shape)
            part_mesh.write('part.stl')
            if visual:
                audit = {'closed_oriented_edges': False, 'notice': 'Visual mesh, not a verified printable solid.'}
            else:
                audit = audit_stl('part.stl', len(shape.Solids), shape.Volume, detail(shape)['bounds_mm'])
            archive.write('part.stl', obj.Name + '.stl')
            reports.append({'feature': obj.Name, **detail(shape), 'stl_audit': audit})
    result = doc.addObject('PartDesign::Feature', 'CADPilotResult')
    result.Shape = Part.makeCompound([obj.Shape for obj in objects]) if len(objects) > 1 else objects[0].Shape.copy()
    shape = result.Shape
    doc.recompute()
    doc.saveAs('/work/model.FCStd')
    gui = ET.Element('Document', SchemaVersion='1', HasExpansion='1')
    ET.SubElement(gui, 'Expand')
    providers = ET.SubElement(gui, 'ViewProviderData', Count=str(len(doc.Objects)))
    for obj in doc.Objects:
        provider = ET.SubElement(providers, 'ViewProvider', name=obj.Name, expanded='0', treeRank='-1')
        properties = ET.SubElement(provider, 'Properties', Count='1', TransientCount='0')
        prop = ET.SubElement(properties, 'Property', name='Visibility', type='App::PropertyBool', status='1')
        ET.SubElement(prop, 'Bool', value='true' if obj == result else 'false')
    with zipfile.ZipFile('model.FCStd', 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('GuiDocument.xml', ET.tostring(gui, encoding='utf-8', xml_declaration=True))
    Part.export([result], '/work/model.step')
    mesh = mesh_for(shape)
    mesh.write('model.stl')
    saved_views = render_views(mesh, '/work', views=(json.loads(Path('build-views.json').read_text())
                                                     if Path('build-views.json').is_file() else None))
    if Path('view-render.png').is_file():
        saved_views.append('render')  # a Blender Cycles beauty view produced during the source build
    report = {'valid_geometry': True, 'valid_solid': (not visual) and len(shape.Solids) == 1, **detail(shape),
              'parts': reports, 'result_object': result.Name, 'faces': face_table(shape),
              'cuts': [], 'references': [], 'requirements_verified': False,
              'representation': ('visual mesh (not a verified printable solid)' if visual else
                                 'faceted mesh converted to BREP' if design['language'] in ('openscad', 'blender-python') else 'analytic BREP'),
              'views': saved_views}
    if visual:
        report['printable'] = False
        report['notice'] = ('Visual Blender result: rendered and animated, but the mesh is not a watertight solid, '
                            'so it is not verified for printing. Make it watertight (SOLIDIFY, close holes, recalc '
                            'normals) to print.')
        report['stl_audit'] = {'closed_oriented_edges': False, 'notice': 'Visual mesh; printability not verified.'}
    else:
        try:
            report['stl_audit'] = audit_stl('model.stl', len(shape.Solids), shape.Volume, report['bounds_mm'])
        except ValueError as error:
            report['stl_audit'] = {'closed_oriented_edges': False, 'error': str(error),
                                   'notice': 'Assembly mesh may contain contacting/overlapping parts. Individually audited STL files are in parts.zip.'}
    Path('geometry.json').write_text(json.dumps(report, allow_nan=False))


def resolve(doc, text, default):
    text = text or default
    match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*)(?::(Face|Edge)([1-9][0-9]*))?', text)
    if not match:
        raise ValueError('Use an object name or Object:FaceN / Object:EdgeN from this revision')
    obj = doc.getObject(match[1])
    if not obj or not hasattr(obj, 'Shape') or obj.Shape.isNull():
        raise ValueError('No such geometry object: ' + match[1])
    shape = obj.Shape
    if match[2]:
        items = shape.Faces if match[2] == 'Face' else shape.Edges
        index = int(match[3])
        if index > len(items):
            raise ValueError('Face/edge index does not exist in this revision')
        shape = items[index-1]
    return shape


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Coordinates must be finite numbers')
    return float(value)


def section_shape(shape, section):
    axis = section['axis']
    if axis not in ('x', 'y', 'z') or section.get('keep', 'below') not in ('below', 'above'):
        raise ValueError('Section needs axis x/y/z and keep below/above')
    at = number(section['at'])
    box = shape.BoundBox
    lo = [box.XMin-1, box.YMin-1, box.ZMin-1]
    hi = [box.XMax+1, box.YMax+1, box.ZMax+1]
    index = 'xyz'.index(axis)
    if section.get('keep', 'below') == 'below':
        hi[index] = min(at, hi[index])
    else:
        lo[index] = max(at, lo[index])
    if any(b <= a for a, b in zip(lo, hi)):
        return Part.Shape()
    return shape.common(Part.makeBox(*(b-a for a, b in zip(lo, hi)), App.Vector(*lo)))


def render(doc, query, default):
    import numpy as np
    from PIL import Image, ImageDraw
    directions = {'iso': [1,-1,1], 'top': [0,0,1], 'bottom': [0,0,-1],
                  'front': [0,-1,0], 'back': [0,1,0], 'right': [1,0,0], 'left': [-1,0,0]}
    direction = query.get('direction') or directions[query.get('view', 'iso')]
    if not isinstance(direction, list) or len(direction) != 3:
        raise ValueError('Camera direction needs three finite numbers')
    forward = np.array([number(v) for v in direction])
    if np.linalg.norm(forward) < 1e-9:
        raise ValueError('Camera direction cannot be zero')
    forward /= np.linalg.norm(forward)
    up = np.array([0., 0., 1.]) if abs(forward[2]) < .99 else np.array([0., 1., 0.])
    right = np.cross(up, forward); right /= np.linalg.norm(right)
    up = np.cross(forward, right)
    rotation = np.array([right, up, forward])
    bodies = query.get('bodies') or [default]
    triangles, colors = [], []
    highlight = query.get('highlight') or ''
    for body in bodies:
        shape = resolve(doc, body, default)
        if query.get('section'):
            shape = section_shape(shape, query['section'])
        if shape.isNull():
            continue
        for i, face in enumerate(shape.Faces, 1):
            points, facets = face.tessellate(.08)
            vertices = np.array([[p.x,p.y,p.z] for p in points])
            if not facets:
                continue
            selected = body == highlight or (not query.get('section') and f'{body}:Face{i}' == highlight)
            triangles.extend(vertices[np.array(facets, dtype=int)])
            colors.extend([(245,105,68) if selected else (195,178,148)] * len(facets))
    if not triangles:
        raise ValueError('The selected bodies/section contain no visible geometry')
    if len(triangles) > 400000:
        raise ValueError('Inspection view exceeds 400000 triangles')
    triangles = np.array(triangles)
    normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:,None], 1e-12)
    camera = triangles @ rotation.T
    flat = camera.reshape(-1,3)
    low, high = flat.min(axis=0), flat.max(axis=0)
    center = (low+high)/2
    size = 800
    xy = (camera[:,:,:2]-center[:2]) * (size*.84/max(*(high-low)[:2], 1e-6)) + size/2
    xy[:,:,1] = size-xy[:,:,1]
    image = Image.new('RGB', (size,size), (248,248,246))
    draw = ImageDraw.Draw(image)
    light = np.array([.3,-.5,.8]); light /= np.linalg.norm(light)
    for i in np.argsort(camera[:,:,2].mean(axis=1)):
        tone = .4 + .6*abs(float(normals[i] @ light))
        draw.polygon([tuple(p) for p in xy[i]], fill=tuple(int(v*tone) for v in colors[i]))
    draw.text((14,14), f'{query.get("view", "custom")} | {", ".join(bodies)}', fill=(50,50,50))
    if query.get('section'):
        draw.text((14,32), 'Section preview: '+str(query['section']), fill=(50,50,50))
    image.save('inspection.png')
    return {'camera_direction': direction, 'bodies': bodies, 'highlight': highlight,
            'section': query.get('section'), 'geometry_modified': False,
            'highlight_notice': 'Face highlighting applies to the original topology; use body highlighting in section views.'}


def inspect():
    query = json.loads(Path('query.json').read_text())
    geometry = json.loads(Path('geometry.json').read_text())
    doc = App.openDocument('/work/model.FCStd')
    default = geometry['result_object']
    kind = query.get('query', 'objects')
    if kind == 'render':
        report = render(doc, query, default)
    elif kind == 'objects':
        report = {'objects': [{'id': obj.Name, 'label': obj.Label, **detail(obj.Shape)} for obj in doc.Objects
                              if hasattr(obj, 'Shape') and not obj.Shape.isNull() and obj.Shape.Volume > 0],
                  'result_object': default, 'parts': geometry.get('parts', [])}
    elif kind == 'faces':
        name = query.get('object') or default
        shape = resolve(doc, name, default)
        offset, limit = query.get('offset', 0), query.get('limit', 30)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Face pagination: offset >= 0, limit 1..100')
        rows = []
        for index, face in enumerate(shape.Faces[offset:offset+limit], offset+1):
            row = face_table(face, 1)[0]
            row.update(id=f'{name}:Face{index}', index=index, surface=type(face.Surface).__name__,
                       center_mm=list(face.CenterOfMass), edge_count=len(face.Edges),
                       min_mm=detail(face)['min_mm'], max_mm=detail(face)['max_mm'])
            rows.append(row)
        report = {'object': name, 'faces': rows, 'total': len(shape.Faces),
                  'next_offset': offset+limit if offset+limit < len(shape.Faces) else None}
    elif kind == 'measure':
        a, b = resolve(doc, query['a'], default), resolve(doc, query['b'], default)
        gap, pairs, _ = a.distToShape(b)
        overlap = a.common(b)
        report = {'a': query['a'], 'b': query['b'], 'minimum_distance_mm': gap,
                  'nearest_points_mm': [[list(x), list(y)] for x,y in pairs[:8]],
                  'intersection_volume_mm3': overlap.Volume, 'intersection_area_mm2': overlap.Area,
                  'scope': 'Minimum geometric distance and intersection of these shapes, not complete fit certification'}
    elif kind == 'section':
        shape = resolve(doc, query.get('object'), default)
        axis, at = query['axis'], number(query['at'])
        if axis not in ('x','y','z'):
            raise ValueError('Section axis is x/y/z')
        vector = App.Vector(*[1 if c == axis else 0 for c in 'xyz'])
        wires = shape.slice(vector, at)
        report = {'object': query.get('object') or default, 'axis': axis, 'at_mm': at,
                  'contours': [{'closed': w.isClosed(), 'length_mm': w.Length,
                                'bounds': detail(w), 'enclosed_area_mm2': Part.Face(w).Area if w.isClosed() else None} for w in wires],
                  'notice': 'Areas are per contour; nested contours can be voids, not additional material.'}
    else:
        raise ValueError('Unknown inspection query')
    Path('inspection.json').write_text(json.dumps({'query': kind, **report}, allow_nan=False))


if __name__ == '__main__':
    build() if sys.argv[1] == 'build' else inspect()
