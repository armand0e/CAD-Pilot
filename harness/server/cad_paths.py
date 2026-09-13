"""SVG path data to native CAD curves. Available as cad_paths inside /work.

Path grammar: https://www.w3.org/TR/SVG/paths.html
Lines and quadratic/cubic Béziers stay native curves. Elliptical arcs use cubic
segments of at most 45 degrees (approximation, not an exact conic).
"""
import math
import re

import FreeCAD as App
import Part

TOKEN = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
ARITY = {'M':2, 'L':2, 'H':1, 'V':1, 'C':6, 'S':4, 'Q':4, 'T':2, 'A':7}


def arc_segments(start, end, rx, ry, degrees, large, sweep):
    """SVG endpoint-to-center conversion followed by cubic arc segments."""
    if large not in (0,1) or sweep not in (0,1):
        raise ValueError('SVG arc flags must be 0 or 1, separated from other numbers')
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(degrees % 360)
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = (start[0]-end[0])/2, (start[1]-end[1])/2
    x, y = c*dx+s*dy, -s*dx+c*dy
    scale = x*x/(rx*rx) + y*y/(ry*ry)
    if scale > 1:
        rx *= math.sqrt(scale); ry *= math.sqrt(scale)
    factor = math.sqrt(max(0, (rx*rx*ry*ry-rx*rx*y*y-ry*ry*x*x)/(rx*rx*y*y+ry*ry*x*x)))
    if large == sweep:
        factor = -factor
    cx, cy = factor*rx*y/ry, -factor*ry*x/rx
    center = (c*cx-s*cy+(start[0]+end[0])/2, s*cx+c*cy+(start[1]+end[1])/2)
    theta = math.atan2((y-cy)/ry, (x-cx)/rx)
    last = math.atan2((-y-cy)/ry, (-x-cx)/rx)
    delta = (last-theta) % (2*math.pi)
    if not sweep and delta > 0:
        delta -= 2*math.pi
    steps = max(1, math.ceil(abs(delta)/(math.pi/4)))
    def point(t):
        return (center[0]+rx*c*math.cos(t)-ry*s*math.sin(t), center[1]+rx*s*math.cos(t)+ry*c*math.sin(t))
    def tangent(t):
        return (-rx*c*math.sin(t)-ry*s*math.cos(t), -rx*s*math.sin(t)+ry*c*math.cos(t))
    for i in range(steps):
        a, b = theta+delta*i/steps, theta+delta*(i+1)/steps
        k = 4/3*math.tan((b-a)/4)
        p, q, u, v = point(a), point(b), tangent(a), tangent(b)
        yield [p, (p[0]+k*u[0],p[1]+k*u[1]), (q[0]-k*v[0],q[1]-k*v[1]), q]


def svg_wires(data, plane='xy', scale=1, flip_y=False):
    if not isinstance(data, str) or not data.strip() or len(data) > 100000:
        raise ValueError('Supply SVG path d data (up to 100000 characters), not XML')
    if TOKEN.sub('', data).strip(' ,\t\n\r'):
        raise ValueError('Unsupported SVG path character or command')
    if plane not in ('xy','xz','yz') or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Use plane xy/xz/yz and a positive finite millimetres-per-unit scale')
    tokens = TOKEN.findall(data)
    index, command, previous = 0, None, None
    current, start, control = (0.,0.), None, None
    edges, wires = [], []
    def vector(p):
        u, v = p[0]*scale, p[1]*scale*(-1 if flip_y else 1)
        return App.Vector(*( (u,v,0) if plane=='xy' else (u,0,v) if plane=='xz' else (0,u,v) ))
    def line(a,b):
        if math.dist(a,b) > 1e-10:
            edges.append(Part.makeLine(vector(a),vector(b)))
    def bezier(points):
        curve = Part.BezierCurve()
        curve.setPoles([vector(p) for p in points])
        edges.append(curve.toShape())
    def close():
        if start is None or not edges:
            raise ValueError('Each path must have a nonempty, closed outline')
        line(current,start)
        wire = Part.Wire(edges)
        if not wire.isClosed() or not wire.isValid():
            raise ValueError('The outline does not form a valid closed wire')
        wires.append(wire)
        edges.clear()
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]; index += 1
        elif command is None:
            raise ValueError('Expected an SVG path command')
        kind = command.upper()
        if kind == 'Z':
            close(); current = start; start = None; control = None; previous = 'Z'; command = None
            continue
        count = ARITY[kind]
        if index+count > len(tokens) or any(t.isalpha() for t in tokens[index:index+count]):
            raise ValueError('Incomplete SVG ' + kind + ' command')
        values = [float(t) for t in tokens[index:index+count]]; index += count
        if any(not math.isfinite(v) for v in values):
            raise ValueError('SVG coordinates must be finite')
        relative = command.islower()
        def point(a,b):
            return (a+current[0],b+current[1]) if relative else (a,b)
        if kind == 'M':
            if edges:
                raise ValueError('Close each SVG subpath with Z before starting another')
            current = point(*values); start = current
            command = 'l' if relative else 'L'
        else:
            if start is None:
                raise ValueError('Each outline must start with M')
            if kind in ('L','H','V'):
                target = point(*values) if kind == 'L' else ((current[0]+values[0] if relative else values[0],current[1]) if kind=='H' else (current[0],current[1]+values[0] if relative else values[0]))
                line(current,target)
            elif kind in ('C','S','Q','T'):
                target = point(*values[-2:])
                if kind == 'C':
                    points = [current,point(*values[:2]),point(*values[2:4]),target]
                    control = points[-2]
                elif kind == 'S':
                    reflected = (2*current[0]-control[0],2*current[1]-control[1]) if previous in ('C','S') else current
                    points = [current,reflected,point(*values[:2]),target]; control = points[-2]
                elif kind == 'Q':
                    control = point(*values[:2]); points = [current,control,target]
                else:
                    control = (2*current[0]-control[0],2*current[1]-control[1]) if previous in ('Q','T') else current
                    points = [current,control,target]
                bezier(points)
            elif kind == 'A':
                target = point(*values[-2:])
                rx,ry,angle,large,sweep = values[:5]
                if math.dist(current,target) > 1e-10:
                    if rx == 0 or ry == 0:
                        line(current,target)
                    else:
                        for points in arc_segments(current,target,rx,ry,angle,large,sweep):
                            bezier(points)
            current = target
        if kind not in ('C','S','Q','T'):
            control = None
        previous = kind
    if edges or start is not None:
        raise ValueError('Close each outline with Z; open drawing paths cannot become a solid')
    if not wires:
        raise ValueError('No closed outline in this path')
    return wires


def svg_face(data, plane='xy', scale=1, flip_y=False):
    # Even/odd nested boundaries become holes; disjoint outlines become separate faces.
    face = Part.makeFace(svg_wires(data,plane,scale,flip_y), 'Part::FaceMakerBullseye')
    if face.isNull() or not face.isValid() or face.Area <= 0:
        raise ValueError('Outline is self-intersecting, degenerate or otherwise invalid')
    return face


def extrude(data, height, plane='xy', scale=1, flip_y=False):
    if not math.isfinite(height) or height == 0:
        raise ValueError('Extrusion height must be finite and nonzero')
    direction = {'xy': (0,0,height), 'xz': (0,-height,0), 'yz': (height,0,0)}[plane]
    return svg_face(data,plane,scale,flip_y).extrude(App.Vector(*direction))


def revolve(data, angle=360, scale=1, flip_y=False):
    if not math.isfinite(angle) or not 0 < angle <= 360:
        raise ValueError('Revolve angle must be in (0,360] degrees')
    return svg_face(data,'xz',scale,flip_y).revolve(App.Vector(0,0,0),App.Vector(0,0,1),angle)
