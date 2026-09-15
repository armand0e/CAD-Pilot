"""SVG path data to native CAD solids. Available as cad_paths inside /work.

Outlines are SVG path d strings (https://www.w3.org/TR/SVG/paths.html): M/L/H/V/C/S/Q/T/A/Z,
absolute or relative, several subpaths for holes (even/odd nesting). Lines and Bézier
curves stay native curves; elliptical A arcs use cubic segments of at most 45 degrees.
Shape generators (rect, circle, slot, polygon, ...) return path strings you can combine.
"""
import math

import FreeCAD as App
import Part

from svg_path import (PathError, analyze, arc_cubics, circle, d_shape, ellipse, hexagon, outline, parse,
                      polygon, preview, rect, slot, transform, translate, with_holes)

__all__ = ['extrude', 'revolve', 'loft', 'pipe', 'cut_through', 'svg_face', 'svg_wires', 'analyze', 'preview',
           'rect', 'circle', 'ellipse', 'slot', 'polygon', 'hexagon', 'd_shape', 'outline', 'with_holes', 'translate', 'PathError']

PLANES = {'xy': lambda u, v: (u, v, 0.0), 'xz': lambda u, v: (u, 0.0, v), 'yz': lambda u, v: (0.0, u, v)}
NORMALS = {'xy': (0, 0, 1), 'xz': (0, -1, 0), 'yz': (1, 0, 0)}


def _check_plane(plane, scale):
    if plane not in PLANES or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Use plane xy/xz/yz and a positive finite millimetres-per-unit scale')


def _wire(segments, plane, closed):
    """FreeCAD edges for one subpath already in plane coordinates."""
    to = PLANES[plane]
    vector = lambda p: App.Vector(*to(p[0], p[1]))
    edges = []

    def bezier(points):
        curve = Part.BezierCurve()
        curve.setPoles([vector(p) for p in points])
        edges.append(curve.toShape())
    for segment in segments:
        kind = segment[0]
        if kind == 'line':
            if math.dist(segment[1], segment[2]) > 1e-10:
                edges.append(Part.makeLine(vector(segment[1]), vector(segment[2])))
        elif kind in ('quad', 'cubic'):
            bezier(segment[1:])
        else:
            for points in arc_cubics(*segment[1:]):
                bezier(points)
    if not edges:
        raise ValueError('An outline has no length')
    wire = Part.Wire(edges)
    if closed and (not wire.isClosed() or not wire.isValid()):
        raise ValueError('The outline does not form a valid closed wire')
    return wire


def svg_wires(data, plane='xy', scale=1, flip_y=False, *, require_closed=True):
    _check_plane(plane, scale)
    try:
        subpaths, _ = parse(data, require_closed=require_closed)
    except PathError as error:
        raise ValueError(str(error)) from None
    subpaths = transform(subpaths, scale=scale, flip_y=flip_y)
    return [_wire(path['segments'], plane, path.get('closed', True)) for path in subpaths]


def svg_face(data, plane='xy', scale=1, flip_y=False):
    """Even/odd nested outlines become holes; disjoint outlines become separate faces."""
    report = analyze(data, scale=scale, flip_y=flip_y)
    problems = [w for w in report['warnings'] if 'cross' in w or 'no area' in w]
    if problems:
        raise ValueError(' '.join(problems))
    face = Part.makeFace(svg_wires(data, plane, scale, flip_y), 'Part::FaceMakerBullseye')
    if face.isNull() or not face.isValid() or face.Area <= 0:
        raise ValueError('Outline is self-intersecting, degenerate or otherwise invalid')
    return face


def extrude(data, height, plane='xy', scale=1, flip_y=False):
    """Extrude the outline by height along the plane normal: xy -> +z, xz -> -y, yz -> +x."""
    if not math.isfinite(height) or height == 0:
        raise ValueError('Extrusion height must be finite and nonzero')
    n = NORMALS[plane] if plane in NORMALS else (0, 0, 1)
    _check_plane(plane, scale)
    return svg_face(data, plane, scale, flip_y).extrude(App.Vector(n[0] * height, n[1] * height, n[2] * height))


def revolve(data, angle=360, scale=1, flip_y=False):
    """Revolve a closed radius/height profile drawn in XZ (x = radius, y = height) around +Z."""
    if not math.isfinite(angle) or not 0 < angle <= 360:
        raise ValueError('Revolve angle must be in (0,360] degrees')
    return svg_face(data, 'xz', scale, flip_y).revolve(App.Vector(0, 0, 0), App.Vector(0, 0, 1), angle)


def loft(sections, heights, plane='xy', scale=1, flip_y=False, ruled=False):
    """Solid through outlines placed at successive heights along the plane normal.

    sections: path strings (one closed outline each, no holes). heights: one offset per
    section, e.g. loft([rect(40, 30, 4, center=True), circle(20)], [0, 60]) for a vase.
    """
    if not isinstance(sections, (list, tuple)) or len(sections) < 2 or len(sections) != len(heights):
        raise ValueError('loft needs at least two outlines and one height per outline')
    if len({round(h, 9) for h in heights}) != len(heights):
        raise ValueError('loft heights must be distinct')
    _check_plane(plane, scale)
    n = NORMALS[plane]
    wires = []
    for data, height in zip(sections, heights):
        outline_wires = svg_wires(data, plane, scale, flip_y)
        if len(outline_wires) != 1:
            raise ValueError('Each loft section must be a single closed outline without holes')
        wire = outline_wires[0].copy()
        wire.translate(App.Vector(n[0] * height, n[1] * height, n[2] * height))
        wires.append(wire)
    solid = Part.makeLoft(wires, True, ruled)
    if solid.isNull() or not solid.isValid() or solid.Volume <= 0:
        raise ValueError('Loft produced no valid solid; keep sections similar in vertex count and orientation')
    return solid


def pipe(spine, diameter, plane='xy', scale=1, flip_y=False):
    """Round tube/rod of the given diameter swept along an open or closed spine path drawn in the plane."""
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError('pipe needs a positive diameter')
    wires = svg_wires(spine, plane, scale, flip_y, require_closed=False)
    if len(wires) != 1:
        raise ValueError('pipe needs exactly one spine path')
    path = wires[0]
    start = path.Edges[0].valueAt(path.Edges[0].FirstParameter)
    direction = path.Edges[0].tangentAt(path.Edges[0].FirstParameter)
    profile = Part.Wire(Part.makeCircle(diameter / 2, start, direction))
    shape = path.makePipeShell([profile], True, True)
    if shape.isNull() or not shape.isValid() or shape.Volume <= 0:
        raise ValueError('The spine is too tight for this diameter or self-intersects')
    return shape


def cut_through(shape, data, plane='xy', at=(0, 0, 0), depth=None, scale=1, flip_y=False):
    """Cut an outline into shape: the outline sits on the plane through `at`; it cuts `depth`
    along the plane normal, or completely through the shape when depth is None."""
    _check_plane(plane, scale)
    n = NORMALS[plane]
    box = shape.BoundBox
    span = box.DiagonalLength + 2
    if depth is None:
        cutter = svg_face(data, plane, scale, flip_y).extrude(App.Vector(n[0] * 2 * span, n[1] * 2 * span, n[2] * 2 * span))
        cutter.translate(App.Vector(-n[0] * span, -n[1] * span, -n[2] * span))
    else:
        cutter = extrude(data, depth, plane, scale, flip_y)
    cutter.translate(App.Vector(*at))
    result = shape.cut(cutter)
    if result.isNull() or not result.isValid():
        raise ValueError('The cut produced invalid geometry')
    if abs(result.Volume - shape.Volume) < 1e-9:
        raise ValueError('The cut removed no material: check the plane, position (at) and depth against the body')
    return result


def gear(module, teeth, pressure_angle=20.0, samples=8):
    """SVG path outline of an involute spur gear, ready to extrude. `module` is the pitch
    diameter divided by the tooth count (mm/tooth), `teeth` >= 5, `pressure_angle` in degrees.
    Standard proportions: addendum = module, dedendum = 1.25*module. Add a bore with
    with_holes(gear(...), circle(bore_d, (0, 0))). Ordinary extruded geometry — it meshes
    cleanly, unlike a helical thread."""
    m = module
    z = int(teeth)
    if not isinstance(module, (int, float)) or not math.isfinite(m) or m <= 0:
        raise ValueError('gear module must be positive (pitch diameter / teeth)')
    if teeth != z or z < 5 or z > 400:
        raise ValueError('gear teeth must be a whole number from 5 to 400')
    if not 5 <= pressure_angle <= 35:
        raise ValueError('gear pressure_angle must be 5-35 degrees')
    alpha = math.radians(pressure_angle)
    rp = m * z / 2.0                    # pitch radius
    rb = rp * math.cos(alpha)           # base radius
    ra = rp + m                         # addendum (outer) radius
    rf = max(rp - 1.25 * m, 0.2 * m)    # root radius
    inv_a = math.tan(alpha) - alpha
    half = math.pi / (2.0 * z)          # tooth half-thickness angle at the pitch circle

    def flank(sign):
        # One involute flank from the root/base up to the addendum, sampled by radius.
        start = max(rb, rf)
        points = []
        for i in range(samples + 1):
            r = start + (ra - start) * i / samples
            ratio = min(1.0, rb / r)
            inv_r = math.tan(math.acos(ratio)) - math.acos(ratio)
            phi = half + inv_a - inv_r     # angle from the tooth centre line
            points.append((r * math.cos(sign * phi), r * math.sin(sign * phi)))
        if rf < rb:                        # radial line down into the root gap
            phi0 = half + inv_a
            points.insert(0, (rf * math.cos(sign * phi0), rf * math.sin(sign * phi0)))
        return points

    pts = []
    for k in range(z):
        base = 2.0 * math.pi * k / z
        cos_b, sin_b = math.cos(base), math.sin(base)
        tooth = flank(-1) + list(reversed(flank(1)))   # up the right flank, over the tip, down the left
        for x, y in tooth:
            pts.append((x * cos_b - y * sin_b, x * sin_b + y * cos_b))
    d = 'M %.4f %.4f ' % pts[0] + ' '.join('L %.4f %.4f' % p for p in pts[1:]) + ' Z'
    return d


def rack(module, teeth, pressure_angle=20.0, base=None):
    """SVG path outline of an involute gear rack (the straight mate for a spur gear of the
    same module), ready to extrude. Teeth run along +x with crests up at +y; `module` is the
    pitch (pi*module apart), `teeth` the count, `base` the solid bar height below the roots
    (default 2*module). Pairs with gear(module, ...). Ordinary geometry; meshes cleanly."""
    m = module
    n = int(teeth)
    if not isinstance(module, (int, float)) or not math.isfinite(m) or m <= 0:
        raise ValueError('rack module must be positive')
    if teeth != n or n < 1 or n > 400:
        raise ValueError('rack teeth must be a whole number from 1 to 400')
    if not 5 <= pressure_angle <= 35:
        raise ValueError('rack pressure_angle must be 5-35 degrees')
    base_h = 2.0 * m if base is None else base
    if not isinstance(base_h, (int, float)) or base_h <= 0:
        raise ValueError('rack base must be a positive height')
    p = math.pi * m                                   # circular pitch
    add, ded, ta = m, 1.25 * m, math.tan(math.radians(pressure_angle))
    y_top, y_root, y_bottom = add, -1.25 * m, -1.25 * m - base_h
    length = n * p
    top = []
    for i in range(n):
        xc = (i + 0.5) * p
        top += [(xc - (p / 4 + ded * ta), y_root), (xc - (p / 4 - add * ta), y_top),
                (xc + (p / 4 - add * ta), y_top), (xc + (p / 4 + ded * ta), y_root)]
    pts = [(0.0, y_bottom), (length, y_bottom), (length, y_root)] + list(reversed(top)) + [(0.0, y_root)]
    d = 'M %.4f %.4f ' % pts[0] + ' '.join('L %.4f %.4f' % q for q in pts[1:]) + ' Z'
    return d
