"""Deterministic binary-STL edge/orientation/component audit, independent of Mesh.

This certifies closed oriented mesh topology and measurements, not printability or
arbitrary triangle self-intersection. The source BREP is separately kernel-validated.
"""
import math
import struct
from pathlib import Path


def audit_stl(path, expected_solids, expected_volume, expected_bounds):
    data = Path(path).read_bytes()
    if len(data) < 84:
        raise ValueError('STL header is truncated')
    count = struct.unpack_from('<I', data, 80)[0]
    if not 4 <= count <= 500000 or len(data) != 84 + count * 50:
        raise ValueError('STL must be a bounded binary triangle mesh (at most 500000 facets)')
    vertices, edges, parent, signed_volumes = {}, {}, list(range(count)), []
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    origin = None
    degenerate, skipped = 0, set()

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for face in range(count):
        record = struct.unpack_from('<12fH', data, 84 + 50 * face)
        points = [tuple(record[j:j + 3]) for j in (3, 6, 9)]
        if not all(math.isfinite(v) for p in points for v in p):
            raise ValueError('STL contains non-finite coordinates')
        if origin is None:
            origin = points[0]
        ids = []
        for point in points:
            ids.append(vertices.setdefault(point, len(vertices)))
            minimum = [min(a, b) for a, b in zip(minimum, point)]
            maximum = [max(a, b) for a, b in zip(maximum, point)]
        u = [b - a for a, b in zip(points[0], points[1])]
        v = [b - a for a, b in zip(points[0], points[2])]
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        if len(set(ids)) != 3:
            # A sliver whose corners merged: its two real edges would pair with each
            # other, so the neighbours already close across it. Skip it entirely.
            degenerate += 1
            skipped.add(face)
            signed_volumes.append(0.0)
            continue
        if sum(x*x for x in cross) == 0:
            # Zero-area triangle with three distinct corners (collinear tessellation of
            # a curved seam): it still pairs its edges consistently and adds no volume.
            degenerate += 1
        signed_volumes.append(sum((p - o) * c for p, o, c in zip(points[0], origin, cross)) / 6)
        for a, b in zip(ids, ids[1:] + ids[:1]):
            key = (min(a, b), max(a, b))
            direction = a < b
            if key not in edges:
                edges[key] = (face, direction)
            else:
                previous, previous_direction = edges[key]
                if previous < 0 or direction == previous_direction:
                    raise ValueError('STL has a non-manifold or inconsistently oriented edge')
                parent[root(face)] = root(previous)
                edges[key] = (-1, False)
    if any(value[0] >= 0 for value in edges.values()):
        raise ValueError('STL has open boundary edges')
    volumes = {}
    for i, volume in enumerate(signed_volumes):
        if i in skipped:
            continue  # merged-corner slivers belong to no shell
        component = root(i)
        volumes[component] = volumes.get(component, 0) + volume
    # A solid with a sealed cavity has one positive outer shell and negative
    # inner shells. Do not mistake legitimate interior voids for extra parts.
    if sum(v > 0 for v in volumes.values()) != expected_solids or any(abs(v) < 1e-12 for v in volumes.values()):
        raise ValueError('STL component count or outward orientation disagrees with the native solid')
    volume = sum(volumes.values())
    if abs(volume - expected_volume) > max(1e-5, expected_volume * .003):
        raise ValueError('STL volume differs from the native solid by more than 0.3%')
    bounds = [b - a for a, b in zip(minimum, maximum)]
    if any(abs(a - b) > max(.06, abs(b) * 1e-5) for a, b in zip(bounds, expected_bounds)):
        raise ValueError('STL bounds disagree with native geometry')
    return {'closed_oriented_edges': True, 'boundary_shell_count': len(volumes),
            'exterior_shell_count': expected_solids, 'triangles': count, 'degenerate_triangles': degenerate,
            'volume_mm3': volume, 'bounds_mm': bounds, 'self_intersections_independently_checked': False}
