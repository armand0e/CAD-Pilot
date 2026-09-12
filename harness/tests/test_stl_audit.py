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

    def test_multiple_solids_and_sealed_inner_cavity(self):
        self.assertEqual(self.audit(tetra() + tetra(shift=3), 2, 1/3, (4, 4, 4))['exterior_shell_count'], 2)
        result = self.audit(tetra(scale=4) + tetra(shift=.3, inward=True), 1, 63/6, (4, 4, 4))
        self.assertEqual(result['boundary_shell_count'], 2)
