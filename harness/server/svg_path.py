"""SVG path data without a CAD kernel: parsing, shape generators, analysis and previews.

Shared by cad_paths (which turns the parsed segments into FreeCAD edges inside the
sandbox) and by the path_preview tool (which runs in the server without FreeCAD).
Grammar: https://www.w3.org/TR/SVG/paths.html — M/L/H/V/C/S/Q/T/A/Z, absolute and
relative forms, implicit command repetition and packed arc flags. Coordinates are
millimetres unless a scale is given; flip_y maps SVG's downward y to CAD's upward y.
"""
import io
import math
import re

TOKEN = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
ARITY = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7}
MAX_DATA = 100000


class PathError(ValueError):
    pass


def fmt(value):
    text = f'{value:.4f}'.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


# ---- parsing -------------------------------------------------------------------------
def tokens_of(data):
    if not isinstance(data, str) or not data.strip() or len(data) > MAX_DATA:
        raise PathError(f'Supply SVG path d data (up to {MAX_DATA} characters), not XML')
    leftover = TOKEN.sub('', data).strip(' ,\t\n\r')
    if leftover:
        raise PathError(f'Unsupported SVG path character or command: {leftover[:20]!r}')
    return TOKEN.findall(data)


def parse(data, *, require_closed=True):
    """Segments per subpath in absolute coordinates, plus warnings.

    Each segment is a tuple: ('line', p0, p1), ('quad', p0, c, p1),
    ('cubic', p0, c1, c2, p1) or ('arc', p0, p1, rx, ry, rotation, large, sweep).
    Returns (subpaths, warnings). Subpaths are lists of segments; an unclosed subpath
    is closed with a straight line (SVG fill semantics) and reported as a warning
    unless require_closed is False, in which case it stays open.
    """
    tokens = tokens_of(data)
    index, command, previous = 0, None, None
    current, start, control = (0.0, 0.0), None, None
    segments, subpaths, warnings = [], [], []
    number = 0
    open_flags = []

    def close(implicit):
        nonlocal segments, start
        if start is None:
            return
        if not segments:
            raise PathError(f'Subpath {len(subpaths) + 1} has no segments before Z')
        if math.dist(current, start) > 1e-9:
            if implicit and not require_closed:
                open_flags.append(True)
            else:
                segments.append(('line', current, start))
                if implicit:
                    warnings.append(f'Subpath {len(subpaths) + 1} was not closed with Z; a straight line back to its start was added.')
                open_flags.append(False)
        else:
            open_flags.append(False)
        subpaths.append(segments)
        segments = []

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]; index += 1; number += 1
            if command.upper() == 'Z':
                close(False)
                current = start if start is not None else current
                start, control, previous, command = None, None, 'Z', None
                continue
        elif command is None:
            raise PathError(f'Expected an SVG path command before {tokens[index]!r}' + (' (numbers after Z need a new command)' if previous == 'Z' else ''))
        kind = command.upper()
        count = ARITY[kind]
        values = []
        while len(values) < count:
            if index >= len(tokens) or tokens[index].isalpha():
                raise PathError(f'Incomplete {kind} command (#{number}): expected {count} numbers, got {len(values)}')
            token = tokens[index]
            if kind == 'A' and len(values) in (3, 4) and len(token) > 1 and token[0] in '01' and '.' not in token and 'e' not in token.lower():
                # Packed flags such as "0 01 20 0" or "1120 0": split one flag off.
                values.append(float(token[0]))
                tokens[index] = token[1:]
                continue
            values.append(float(token)); index += 1
        if any(not math.isfinite(v) for v in values):
            raise PathError(f'{kind} command (#{number}) has a non-finite coordinate')
        relative = command.islower()
        point = (lambda a, b: (a + current[0], b + current[1])) if relative else (lambda a, b: (a, b))
        if kind == 'M':
            if segments or start is not None:
                close(True)
            current = point(*values); start = current
            command = 'l' if relative else 'L'
        else:
            if start is None:
                raise PathError(f'{kind} command (#{number}) before any M: each outline must start with M x y')
            if kind in ('L', 'H', 'V'):
                target = (point(*values) if kind == 'L' else
                          ((current[0] + values[0]) if relative else values[0], current[1]) if kind == 'H' else
                          (current[0], (current[1] + values[0]) if relative else values[0]))
                if math.dist(current, target) > 1e-9:
                    segments.append(('line', current, target))
            elif kind in ('C', 'S', 'Q', 'T'):
                target = point(*values[-2:])
                if kind == 'C':
                    c1, c2 = point(*values[:2]), point(*values[2:4])
                    segments.append(('cubic', current, c1, c2, target)); control = c2
                elif kind == 'S':
                    c1 = (2 * current[0] - control[0], 2 * current[1] - control[1]) if previous in ('C', 'S') else current
                    c2 = point(*values[:2])
                    segments.append(('cubic', current, c1, c2, target)); control = c2
                elif kind == 'Q':
                    control = point(*values[:2]); segments.append(('quad', current, control, target))
                else:
                    control = (2 * current[0] - control[0], 2 * current[1] - control[1]) if previous in ('Q', 'T') else current
                    segments.append(('quad', current, control, target))
            else:
                target = point(*values[-2:])
                rx, ry, rotation, large, sweep = values[:5]
                if large not in (0, 1) or sweep not in (0, 1):
                    raise PathError(f'A command (#{number}): the large-arc and sweep flags must be 0 or 1')
                if math.dist(current, target) > 1e-9:
                    if rx == 0 or ry == 0:
                        segments.append(('line', current, target))
                    else:
                        segments.append(('arc', current, target, abs(rx), abs(ry), rotation, int(large), int(sweep)))
            current = target
        if kind not in ('C', 'S', 'Q', 'T'):
            control = None
        previous = kind
    if segments or start is not None:
        close(True)
    if not subpaths:
        raise PathError('No outline found: paths need at least M x y ... Z')
    result = []
    for k, path in enumerate(subpaths):
        if not path:
            raise PathError(f'Subpath {k + 1} is empty')
        result.append({'segments': path, 'closed': not open_flags[k]})
    return result, warnings


def serialize(subpaths):
    """Absolute path data for parsed subpaths (as produced by parse or transformed)."""
    parts = []
    for path in subpaths:
        segments = path['segments']
        parts.append('M' + fmt(segments[0][1][0]) + ' ' + fmt(segments[0][1][1]))
        for segment in segments:
            kind = segment[0]
            if kind == 'line':
                parts.append('L' + fmt(segment[2][0]) + ' ' + fmt(segment[2][1]))
            elif kind == 'quad':
                parts.append('Q' + ' '.join(fmt(v) for p in segment[2:] for v in p))
            elif kind == 'cubic':
                parts.append('C' + ' '.join(fmt(v) for p in segment[2:] for v in p))
            else:
                _, _, p1, rx, ry, rotation, large, sweep = segment
                parts.append(f'A{fmt(rx)} {fmt(ry)} {fmt(rotation)} {large} {sweep} {fmt(p1[0])} {fmt(p1[1])}')
        if path.get('closed', True):
            parts.append('Z')
    return ' '.join(parts)


def transform(subpaths, *, scale=1.0, flip_y=False, dx=0.0, dy=0.0):
    def point(p):
        return (p[0] * scale + dx, p[1] * scale * (-1 if flip_y else 1) + dy)
    result = []
    for path in subpaths:
        segments = []
        for segment in path['segments']:
            if segment[0] == 'arc':
                _, p0, p1, rx, ry, rotation, large, sweep = segment
                segments.append(('arc', point(p0), point(p1), rx * scale, ry * scale, -rotation if flip_y else rotation,
                                 large, 1 - sweep if flip_y else sweep))
            else:
                segments.append((segment[0],) + tuple(point(p) for p in segment[1:]))
        result.append({**path, 'segments': segments})
    return result


# ---- geometry -----------------------------------------------------------------------
def arc_center(p0, p1, rx, ry, rotation, large, sweep):
    """SVG endpoint parameterisation to centre form: (center, rx, ry, theta, delta)."""
    phi = math.radians(rotation % 360)
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x, y = c * dx + s * dy, -s * dx + c * dy
    lam = x * x / (rx * rx) + y * y / (ry * ry)
    if lam > 1:
        rx *= math.sqrt(lam); ry *= math.sqrt(lam)
    factor = math.sqrt(max(0.0, (rx * rx * ry * ry - rx * rx * y * y - ry * ry * x * x) / (rx * rx * y * y + ry * ry * x * x)))
    if large == sweep:
        factor = -factor
    cx, cy = factor * rx * y / ry, -factor * ry * x / rx
    center = (c * cx - s * cy + (p0[0] + p1[0]) / 2, s * cx + c * cy + (p0[1] + p1[1]) / 2)
    theta = math.atan2((y - cy) / ry, (x - cx) / rx)
    last = math.atan2((-y - cy) / ry, (-x - cx) / rx)
    delta = (last - theta) % (2 * math.pi)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    return center, rx, ry, phi, theta, delta


def arc_point(center, rx, ry, phi, t):
    c, s = math.cos(phi), math.sin(phi)
    return (center[0] + rx * c * math.cos(t) - ry * s * math.sin(t), center[1] + rx * s * math.cos(t) + ry * c * math.sin(t))


def arc_cubics(p0, p1, rx, ry, rotation, large, sweep):
    """Cubic Bézier control points approximating the arc in pieces of at most 45 degrees."""
    center, rx, ry, phi, theta, delta = arc_center(p0, p1, rx, ry, rotation, large, sweep)
    c, s = math.cos(phi), math.sin(phi)
    steps = max(1, math.ceil(abs(delta) / (math.pi / 4)))

    def tangent(t):
        return (-rx * c * math.sin(t) - ry * s * math.cos(t), -rx * s * math.sin(t) + ry * c * math.cos(t))
    for i in range(steps):
        a, b = theta + delta * i / steps, theta + delta * (i + 1) / steps
        k = 4 / 3 * math.tan((b - a) / 4)
        p, q, u, v = arc_point(center, rx, ry, phi, a), arc_point(center, rx, ry, phi, b), tangent(a), tangent(b)
        yield [p, (p[0] + k * u[0], p[1] + k * u[1]), (q[0] - k * v[0], q[1] - k * v[1]), q]


def flatten(subpaths, samples=24):
    """Polylines (one per subpath; closed ones omit the repeated start point)."""
    result = []
    for path in subpaths:
        points = []
        for segment in path['segments']:
            kind = segment[0]
            if kind == 'line':
                points.append(segment[1])
            elif kind == 'quad':
                p0, c, p1 = segment[1:]
                for i in range(samples):
                    t = i / samples
                    points.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0],
                                   (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]))
            elif kind == 'cubic':
                p0, c1, c2, p1 = segment[1:]
                for i in range(samples):
                    t = i / samples
                    a, b, cc, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                    points.append((a * p0[0] + b * c1[0] + cc * c2[0] + d * p1[0], a * p0[1] + b * c1[1] + cc * c2[1] + d * p1[1]))
            else:
                _, p0, p1, rx, ry, rotation, large, sweep = segment
                center, rx2, ry2, phi, theta, delta = arc_center(p0, p1, rx, ry, rotation, large, sweep)
                count = max(8, math.ceil(abs(delta) / (math.pi / 72)))
                for i in range(count):
                    points.append(arc_point(center, rx2, ry2, phi, theta + delta * i / count))
        if not path.get('closed', True):
            points.append(path['segments'][-1][2] if path['segments'][-1][0] != 'arc' else path['segments'][-1][2])
            if path['segments'][-1][0] == 'cubic':
                points[-1] = path['segments'][-1][4]
            elif path['segments'][-1][0] == 'quad':
                points[-1] = path['segments'][-1][3]
        result.append(points)
    return result


def signed_area(points):
    return sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2


def contains(polygon, point):
    x, y = point
    inside = False
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % len(polygon)]
        if (y1 > y) != (y2 > y):
            cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < cross:
                inside = not inside
    return inside


def _segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and all(abs(o) > 1e-12 for o in (o1, o2, o3, o4))


def crossings(polylines, limit=6):
    """Where outlines cross themselves or each other, using a grid so long outlines stay fast."""
    edges = []
    for k, points in enumerate(polylines):
        n = len(points)
        for i in range(n if len(points) > 2 else 0):
            edges.append((k, i, points[i], points[(i + 1) % n]))
    if not edges:
        return []
    xs = [p[0] for e in edges for p in e[2:]]; ys = [p[1] for e in edges for p in e[2:]]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    cell = span / 40
    grid = {}
    for e in edges:
        (x1, y1), (x2, y2) = e[2], e[3]
        for gx in range(int((min(x1, x2) - min(xs)) / cell), int((max(x1, x2) - min(xs)) / cell) + 1):
            for gy in range(int((min(y1, y2) - min(ys)) / cell), int((max(y1, y2) - min(ys)) / cell) + 1):
                grid.setdefault((gx, gy), []).append(e)
    found, seen = [], set()
    for bucket in grid.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                key = (a[0], a[1], b[0], b[1])
                if key in seen:
                    continue
                seen.add(key)
                n_a, n_b = len(polylines[a[0]]), len(polylines[b[0]])
                if a[0] == b[0] and (abs(a[1] - b[1]) <= 1 or {a[1], b[1]} == {0, n_a - 1}):
                    continue  # neighbouring edges share a vertex
                if _segments_cross(a[2], a[3], b[2], b[3]):
                    found.append({'subpaths': sorted({a[0] + 1, b[0] + 1}), 'near': [round((a[2][0] + a[3][0]) / 2, 2), round((a[2][1] + a[3][1]) / 2, 2)]})
                    if len(found) >= limit:
                        return found
    return found


def analyze(data, *, scale=1.0, flip_y=False, require_closed=True):
    """Bounds, areas, winding, nesting and problems of an outline, in plane coordinates."""
    parsed, warnings = parse(data, require_closed=require_closed)
    if not math.isfinite(scale) or scale <= 0:
        raise PathError('scale must be a positive number of millimetres per path unit')
    subpaths = transform(parsed, scale=scale, flip_y=flip_y)
    polylines = flatten(subpaths)
    xs = [p[0] for poly in polylines for p in poly]; ys = [p[1] for poly in polylines for p in poly]
    outlines = []
    for k, (path, poly) in enumerate(zip(subpaths, polylines)):
        area = signed_area(poly) if path.get('closed', True) and len(poly) > 2 else 0.0
        depth = sum(1 for j, other in enumerate(polylines) if j != k and len(other) > 2 and subpaths[j].get('closed', True)
                    and all(contains(other, p) for p in poly[:: max(1, len(poly) // 5)]))
        px = [p[0] for p in poly]; py = [p[1] for p in poly]
        outlines.append({'index': k + 1, 'segments': len(path['segments']), 'closed': path.get('closed', True),
                         'bounds': [round(min(px), 3), round(min(py), 3), round(max(px), 3), round(max(py), 3)],
                         'area_mm2': round(abs(area), 3), 'winding': 'ccw' if area > 0 else 'cw' if area < 0 else 'none',
                         'role': 'open' if not path.get('closed', True) else 'hole' if depth % 2 else 'outer', 'depth': depth})
    for o in outlines:
        if o['closed'] and o['area_mm2'] < 1e-6:
            warnings.append(f"Subpath {o['index']} encloses no area (collinear or degenerate).")
        size = max(o['bounds'][2] - o['bounds'][0], o['bounds'][3] - o['bounds'][1])
        if o['closed'] and 0 < size < 0.2:
            warnings.append(f"Subpath {o['index']} is only {size:.2f} mm across; probably a stray point.")
    for cross in crossings(polylines):
        which = cross['subpaths']
        warnings.append((f'Subpath {which[0]} crosses itself' if len(which) == 1 else f'Subpaths {which[0]} and {which[1]} cross each other')
                        + f" near ({cross['near'][0]}, {cross['near'][1]}); a self-intersecting outline cannot become a solid.")
    total = sum(o['area_mm2'] * (-1 if o['role'] == 'hole' else 1) for o in outlines if o['closed'])
    first = subpaths[0]['segments'][0][1]
    return {'subpaths': len(outlines), 'outlines': outlines,
            'bounds': {'min': [round(min(xs), 3), round(min(ys), 3)], 'max': [round(max(xs), 3), round(max(ys), 3)],
                       'size': [round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)]},
            'area_mm2': round(total, 3), 'start': [round(first[0], 3), round(first[1], 3)],
            'holes': sum(o['role'] == 'hole' for o in outlines), 'warnings': warnings,
            'valid': not any('cross' in w or 'no area' in w for w in warnings) and all(o['closed'] for o in outlines),
            'normalized': serialize(subpaths)}


# ---- generators (absolute path data, closed) ------------------------------------------
def _at(at, center, width, height):
    x, y = at
    return (x - width / 2, y - height / 2) if center else (x, y)


def rect(width, height, radius=0.0, at=(0.0, 0.0), center=False):
    """Rectangle (optionally rounded) with its corner or centre at `at`, counter-clockwise."""
    if width <= 0 or height <= 0 or radius < 0 or radius * 2 > min(width, height) + 1e-9:
        raise PathError('rect needs positive width/height and a radius of at most half the shorter side')
    x, y = _at(at, center, width, height)
    if radius == 0:
        return f'M{fmt(x)} {fmt(y)} H{fmt(x + width)} V{fmt(y + height)} H{fmt(x)} Z'
    r = fmt(radius)
    return (f'M{fmt(x + radius)} {fmt(y)} H{fmt(x + width - radius)} A{r} {r} 0 0 1 {fmt(x + width)} {fmt(y + radius)} '
            f'V{fmt(y + height - radius)} A{r} {r} 0 0 1 {fmt(x + width - radius)} {fmt(y + height)} H{fmt(x + radius)} '
            f'A{r} {r} 0 0 1 {fmt(x)} {fmt(y + height - radius)} V{fmt(y + radius)} A{r} {r} 0 0 1 {fmt(x + radius)} {fmt(y)} Z')


def circle(diameter, at=(0.0, 0.0)):
    if diameter <= 0:
        raise PathError('circle needs a positive diameter')
    r, (x, y) = diameter / 2, at
    return f'M{fmt(x - r)} {fmt(y)} A{fmt(r)} {fmt(r)} 0 1 0 {fmt(x + r)} {fmt(y)} A{fmt(r)} {fmt(r)} 0 1 0 {fmt(x - r)} {fmt(y)} Z'


def ellipse(width, height, at=(0.0, 0.0)):
    if width <= 0 or height <= 0:
        raise PathError('ellipse needs positive width and height')
    rx, ry, (x, y) = width / 2, height / 2, at
    return f'M{fmt(x - rx)} {fmt(y)} A{fmt(rx)} {fmt(ry)} 0 1 0 {fmt(x + rx)} {fmt(y)} A{fmt(rx)} {fmt(ry)} 0 1 0 {fmt(x - rx)} {fmt(y)} Z'


def slot(length, width, at=(0.0, 0.0), center=False, vertical=False):
    """Stadium: overall length including the round ends; corner/centre at `at`."""
    if length <= width or width <= 0:
        raise PathError('slot needs width > 0 and length greater than width')
    if vertical:
        length, width = width, length
    x, y = _at(at, center, length, width)
    r = fmt((width if not vertical else length) / 2)
    if not vertical:
        rr = width / 2
        return (f'M{fmt(x + rr)} {fmt(y)} H{fmt(x + length - rr)} A{r} {r} 0 0 1 {fmt(x + length - rr)} {fmt(y + width)} '
                f'H{fmt(x + rr)} A{r} {r} 0 0 1 {fmt(x + rr)} {fmt(y)} Z')
    rr = length / 2
    return (f'M{fmt(x + length)} {fmt(y + rr)} V{fmt(y + width - rr)} A{r} {r} 0 0 1 {fmt(x)} {fmt(y + width - rr)} '
            f'V{fmt(y + rr)} A{r} {r} 0 0 1 {fmt(x + length)} {fmt(y + rr)} Z')


def polygon(sides, diameter=None, across_flats=None, at=(0.0, 0.0), rotation=0.0):
    """Regular polygon by circumscribed diameter or across-flats size; rotation in degrees."""
    if not isinstance(sides, int) or sides < 3 or sides > 360:
        raise PathError('polygon needs 3 to 360 sides')
    if diameter is None and across_flats is None:
        raise PathError('polygon needs diameter (across corners) or across_flats')
    radius = diameter / 2 if diameter is not None else across_flats / 2 / math.cos(math.pi / sides)
    if radius <= 0:
        raise PathError('polygon size must be positive')
    x, y = at
    base = math.radians(rotation) + (math.pi / sides if across_flats is not None and sides % 2 == 0 else 0)
    points = [(x + radius * math.cos(base + 2 * math.pi * i / sides), y + radius * math.sin(base + 2 * math.pi * i / sides)) for i in range(sides)]
    return outline(points)


def hexagon(across_flats, at=(0.0, 0.0), rotation=0.0):
    return polygon(6, across_flats=across_flats, at=at, rotation=rotation)


def d_shape(diameter, flat_depth, at=(0.0, 0.0), rotation=0.0):
    """Circle with one flat: flat_depth is how much is cut off the diameter (D-shaft holes)."""
    r = diameter / 2
    if diameter <= 0 or not 0 < flat_depth < diameter:
        raise PathError('d_shape needs a positive diameter and a flat depth smaller than it')
    h = r - flat_depth
    half = math.sqrt(max(0.0, r * r - h * h))
    angle = math.radians(rotation)
    c, s = math.cos(angle), math.sin(angle)
    def rot(px, py):
        return (at[0] + px * c - py * s, at[1] + px * s + py * c)
    p0, p1 = rot(h, -half), rot(h, half)
    large = 1 if flat_depth < r else 0
    return f'M{fmt(p0[0])} {fmt(p0[1])} L{fmt(p1[0])} {fmt(p1[1])} A{fmt(r)} {fmt(r)} 0 {large} 1 {fmt(p0[0])} {fmt(p0[1])} Z'


def outline(points):
    if len(points) < 3:
        raise PathError('outline needs at least three points')
    return 'M' + ' L'.join(f'{fmt(x)} {fmt(y)}' for x, y in points) + ' Z'


def with_holes(shape, *holes):
    """One path with nested subpaths; even/odd nesting makes the inner outlines holes."""
    return ' '.join([shape, *holes])


def translate(path, dx, dy):
    parsed, _ = parse(path, require_closed=False)
    return serialize(transform(parsed, dx=dx, dy=dy))


def scale_path(path, factor):
    parsed, _ = parse(path, require_closed=False)
    return serialize(transform(parsed, scale=factor))


# ---- preview -------------------------------------------------------------------------
def preview(data, *, scale=1.0, flip_y=False, plane='xy', size=720, require_closed=True):
    """PNG showing the outline in plane coordinates with a millimetre grid, start and direction."""
    from PIL import Image, ImageDraw
    report = analyze(data, scale=scale, flip_y=flip_y, require_closed=require_closed)
    parsed, _ = parse(data, require_closed=require_closed)
    polylines = flatten(transform(parsed, scale=scale, flip_y=flip_y))
    (x0, y0), (x1, y1) = report['bounds']['min'], report['bounds']['max']
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    margin = 64
    unit = (size - 2 * margin) / max(w, h)
    step = next(s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if s * unit >= 36)
    ox = margin + ((size - 2 * margin) - w * unit) / 2 - x0 * unit
    oy = size - margin - ((size - 2 * margin) - h * unit) / 2 + y0 * unit

    def px(p):
        return (ox + p[0] * unit, oy - p[1] * unit)
    image = Image.new('RGB', (size, size), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    gx = math.floor((x0 - margin / unit) / step) * step
    while gx <= x1 + margin / unit:
        x = ox + gx * unit
        if 0 <= x <= size:
            draw.line([(x, 0), (x, size)], fill=(228, 228, 224), width=1)
            draw.text((x + 2, size - 14), fmt(gx), fill=(120, 120, 120))
        gx += step
    gy = math.floor((y0 - margin / unit) / step) * step
    while gy <= y1 + margin / unit:
        y = oy - gy * unit
        if 0 <= y <= size:
            draw.line([(0, y), (size, y)], fill=(228, 228, 224), width=1)
            draw.text((3, y - 12), fmt(gy), fill=(120, 120, 120))
        gy += step
    if 0 <= ox <= size:
        draw.line([(ox, 0), (ox, size)], fill=(170, 170, 165), width=2)
    if 0 <= oy <= size:
        draw.line([(0, oy), (size, oy)], fill=(170, 170, 165), width=2)
    for depth in range(0, 8):
        for outline_info, poly in zip(report['outlines'], polylines):
            if outline_info['depth'] != depth or len(poly) < 3 or not outline_info['closed']:
                continue
            draw.polygon([px(p) for p in poly], fill=(250, 250, 248) if depth % 2 else (205, 188, 160))
    for k, (outline_info, poly) in enumerate(zip(report['outlines'], polylines)):
        pts = [px(p) for p in poly]
        if outline_info['closed'] and len(pts) > 1:
            pts.append(pts[0])
        draw.line(pts, fill=(60, 60, 60), width=2)
        start = pts[0]
        draw.ellipse([start[0] - 5, start[1] - 5, start[0] + 5, start[1] + 5], fill=(40, 160, 70))
        if len(pts) > 1:
            a, b = pts[0], pts[min(3, len(pts) - 1)]
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            tip = (a[0] + 16 * math.cos(angle), a[1] + 16 * math.sin(angle))
            draw.line([a, tip], fill=(40, 160, 70), width=3)
            draw.polygon([tip, (tip[0] - 7 * math.cos(angle - 0.5), tip[1] - 7 * math.sin(angle - 0.5)),
                          (tip[0] - 7 * math.cos(angle + 0.5), tip[1] - 7 * math.sin(angle + 0.5))], fill=(40, 160, 70))
        label = f"{k + 1}{' hole' if outline_info['role'] == 'hole' else ''}"
        if outline_info['role'] == 'hole' or len(report['outlines']) == 1:
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
        else:
            # An outer outline's centroid is often inside one of its holes; label it by its start instead.
            cx, cy = pts[0][0] + 12, pts[0][1] - 22
        draw.text((cx - 4, cy - 6), label, fill=(30, 30, 30))
    axes = {'xy': ('x', 'y'), 'xz': ('x', 'z'), 'yz': ('y', 'z')}[plane]
    draw.text((12, 10), f"{axes[0]} right, {axes[1]} up · {fmt(w)} x {fmt(h)} mm · grid {fmt(step)} mm · green = start and direction", fill=(40, 40, 40))
    draw.text((12, 26), f"bounds {fmt(x0)}..{fmt(x1)} x {fmt(y0)}..{fmt(y1)} · area {fmt(report['area_mm2'])} mm2 · {report['subpaths']} subpath(s), {report['holes']} hole(s)", fill=(40, 40, 40))
    for i, warning in enumerate(report['warnings'][:3]):
        draw.text((12, 44 + 14 * i), 'Warning: ' + warning[:110], fill=(190, 50, 40))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG', optimize=True)
    return buffer.getvalue(), report
