import struct
import tempfile
import unittest
from pathlib import Path
from server.stl_audit import audit_stl


def tetra(scale=1, shift=0, inward=False):
    points = [(shift, shift, shift), (scale+shift, shift, shift),
              (shift, scale+shift, shift), (shift, shift, scale+shift)]
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    return [[points[i] for i in (face[::-1] if inward else face)] for face in faces]


def subdivide(faces, levels):
    """Midpoint-subdivide each triangle into 4, keeping the solid's volume and bounds
    exact while multiplying facet count — a stand-in for fine curved geometry."""
    def mid(p, q):
        return tuple((a + b) / 2 for a, b in zip(p, q))
    for _ in range(levels):
        out = []
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]]
        faces = out
    return faces


class STLTests(unittest.TestCase):
    def audit(self, faces, solids=1, volume=1/6, bounds=(1, 1, 1)):
        data = bytes(80) + struct.pack('<I', len(faces))
        for face in faces:
            data += struct.pack('<12fH', 0, 0, 0, *(v for point in face for v in point), 0)
        with tempfile.TemporaryDirectory(prefix='stl-audit-test-') as root:
            path = Path(root) / 'model.stl'; path.write_bytes(data)
            return audit_stl(path, solids, volume, bounds)

    def test_valid_oriented_mesh(self):
        self.assertTrue(self.audit(tetra())['closed_oriented_edges'])

    def test_open_nonmanifold_inverted_and_degenerate_rejected(self):
        good = tetra()
        cases = [good[:3] + [good[2]], good + [good[0]], [good[0][::-1]] + good[1:],
                 [[good[0][0]] * 3] + good[1:], tetra(inward=True)]
        for faces in cases:
            with self.subTest(faces=faces), self.assertRaises(ValueError):
                self.audit(faces)

    def test_independent_bounds_volume_and_solid_count_checks(self):
        for kwargs in ({'volume': 1}, {'bounds': (2, 1, 1)}, {'solids': 2}):
            with self.assertRaises(ValueError):
                self.audit(tetra(), **kwargs)

    def test_volume_tolerance_scales_with_mesh_complexity(self):
        # A fine helical/curved mesh differs from its analytic volume by ~2% at any
        # tessellation; a prismatic part stays under ~0.3%. The tolerance must follow
        # complexity so a correct thread is not rejected while simple parts stay strict.
        fine = subdivide(tetra(), 6)  # 16384 coplanar facets, exact volume 1/6
        self.assertGreater(len(fine), 15000)
        off = (1 / 6) * 1.006         # a 0.6% volume discrepancy
        with self.assertRaises(ValueError):      # 4 facets -> held near 0.3%
            self.audit(tetra(), volume=off)
        self.assertTrue(self.audit(fine, volume=off)['closed_oriented_edges'])  # complex -> tolerated

    def test_multiple_solids_and_sealed_inner_cavity(self):
        self.assertEqual(self.audit(tetra() + tetra(shift=3), 2, 1/3, (4, 4, 4))['exterior_shell_count'], 2)
        result = self.audit(tetra(scale=4) + tetra(shift=.3, inward=True), 1, 63/6, (4, 4, 4))
        self.assertEqual(result['boundary_shell_count'], 2)
