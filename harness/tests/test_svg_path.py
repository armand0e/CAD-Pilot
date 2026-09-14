"""SVG path parsing, generators, analysis and previews without a CAD kernel."""
import io
import math
import unittest

from PIL import Image

from server import svg_path as S


class ParserTests(unittest.TestCase):
    def test_all_commands_relative_forms_and_implicit_repetition(self):
        paths, warnings = S.parse('m10 10 h20 v5 l-5 5 -5 0 c0 5 5 5 5 0 s5 -5 5 0 q5 5 0 10 t-5 0 a3 3 0 0 1 -6 0 z')
        self.assertEqual(warnings, [])
        self.assertEqual(len(paths), 1)
        kinds = [seg[0] for seg in paths[0]['segments']]
        self.assertEqual(kinds, ['line', 'line', 'line', 'line', 'cubic', 'cubic', 'quad', 'quad', 'arc', 'line'])
        self.assertEqual(paths[0]['segments'][0][1], (10.0, 10.0))

    def test_packed_arc_flags_and_unclosed_subpath_warning(self):
        paths, warnings = S.parse('M0 10 a10 10 0 1120 0 a10 10 0 11-20 0 M30 0 L40 0 L40 10')
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0]['segments'][0][0], 'arc')
        self.assertTrue(paths[1]['closed'])
        self.assertEqual(paths[1]['segments'][-1], ('line', (40.0, 10.0), (30.0, 0.0)))
        self.assertIn('not closed with Z', warnings[0])
        paths, _ = S.parse('M0 0 L10 0 L10 10', require_closed=False)
        self.assertFalse(paths[0]['closed'])

    def test_errors_name_the_command(self):
        for data, message in [('M0 0 L10', 'Incomplete L command (#2)'), ('L0 0 Z', 'before any M'), ('M0 0 L1 1 Z 5 5', 'numbers after Z'),
                              ('M0 0 X5', 'Unsupported SVG path character'), ('M0 0 A1 1 0 2 0 5 5 Z', 'flags must be 0 or 1'), ('', 'Supply SVG path')]:
            with self.subTest(data=data):
                with self.assertRaises(S.PathError) as caught:
                    S.parse(data)
                self.assertIn(message, str(caught.exception))

    def test_serialize_round_trips_and_transforms(self):
        data = 'M0 0 L10 0 A5 5 0 0 1 10 10 Q5 15 0 10 Z'
        paths, _ = S.parse(data)
        again, _ = S.parse(S.serialize(paths))
        self.assertEqual(paths, again)
        moved, _ = S.parse(S.translate(data, 3, -2))
        self.assertEqual(moved[0]['segments'][0][1], (3.0, -2.0))
        flipped = S.transform(paths, flip_y=True)
        self.assertEqual(flipped[0]['segments'][1][7], 0)  # sweep flips with the axis
        self.assertEqual(S.scale_path('M0 0 H2 V1 Z', 2.5), 'M0 0 L5 0 L5 2.5 L0 0 Z')


class AnalysisTests(unittest.TestCase):
    def test_areas_holes_winding_and_bounds(self):
        report = S.analyze(S.with_holes(S.rect(40, 25, 3), S.circle(3.2, (6, 6)), S.slot(12, 4, (20, 10))))
        self.assertEqual((report['subpaths'], report['holes']), (3, 2))
        self.assertEqual([o['role'] for o in report['outlines']], ['outer', 'hole', 'hole'])
        self.assertEqual(report['bounds']['size'], [40.0, 25.0])
        expected = 40 * 25 - (4 - math.pi) * 9 - math.pi * 1.6 ** 2 - (8 * 4 + math.pi * 4)
        self.assertAlmostEqual(report['area_mm2'], expected, delta=0.3)
        self.assertTrue(report['valid'])
        self.assertEqual(S.analyze(S.circle(10))['outlines'][0]['winding'], 'cw')
        self.assertEqual(S.analyze(S.rect(10, 5))['outlines'][0]['winding'], 'ccw')
        nested = S.analyze(S.with_holes(S.rect(50, 50), S.rect(30, 30, at=(10, 10)), S.rect(10, 10, at=(20, 20))))
        self.assertEqual([o['role'] for o in nested['outlines']], ['outer', 'hole', 'outer'])
        self.assertAlmostEqual(nested['area_mm2'], 2500 - 900 + 100, delta=0.01)

    def test_self_intersection_and_crossing_holes_are_reported(self):
        report = S.analyze('M0 0 L10 10 L10 0 L0 10 Z')
        self.assertFalse(report['valid'])
        self.assertTrue(any('crosses itself near (5.0, 5.0)' in w for w in report['warnings']))
        report = S.analyze(S.with_holes(S.rect(20, 20), S.rect(10, 10, at=(15, 5))))
        self.assertTrue(any('cross each other' in w for w in report['warnings']))
        self.assertTrue(S.analyze(S.with_holes(S.rect(20, 20), S.rect(10, 10, at=(5, 5))))['valid'])

    def test_generators_are_valid_and_sized(self):
        cases = [(S.rect(40, 25, 3), (40, 25)), (S.rect(10, 6, at=(5, 5), center=True), (10, 6)), (S.circle(10, (5, 5)), (10, 10)),
                 (S.ellipse(20, 10), (20, 10)), (S.slot(20, 6), (20, 6)), (S.slot(20, 6, vertical=True), (6, 20)),
                 (S.hexagon(5.5), (5.5, 6.351)), (S.polygon(4, diameter=10, rotation=45), (7.071, 7.071)),
                 (S.d_shape(6, 1), (5, 5.996)), (S.outline([(0, 0), (10, 0), (5, 8)]), (10, 8))]
        for path, size in cases:
            with self.subTest(path=path):
                report = S.analyze(path)
                self.assertTrue(report['valid'], report['warnings'])
                for actual, expected in zip(report['bounds']['size'], size):
                    self.assertAlmostEqual(actual, expected, delta=0.005)  # bounds come from sampled curves
        self.assertAlmostEqual(S.analyze(S.circle(10))['area_mm2'], math.pi * 25, delta=0.05)
        centred = S.analyze(S.rect(10, 6, at=(5, 5), center=True))['bounds']
        self.assertEqual((centred['min'], centred['max']), ([0.0, 2.0], [10.0, 8.0]))
        for bad in (lambda: S.rect(10, 5, 3), lambda: S.slot(5, 6), lambda: S.polygon(2, diameter=5), lambda: S.d_shape(6, 6)):
            with self.assertRaises(S.PathError):
                bad()

    def test_scale_and_flip_y_apply_before_analysis(self):
        report = S.analyze('M0 0 L100 0 L100 50 Z', scale=0.1, flip_y=True)
        self.assertEqual(report['bounds']['min'], [0.0, -5.0])
        self.assertEqual(report['bounds']['size'], [10.0, 5.0])

    def test_preview_renders_a_png_with_the_grid(self):
        png, report = S.preview(S.with_holes(S.rect(40, 25, 3), S.circle(3.2, (6, 6))), plane='xz')
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (720, 720))
        self.assertEqual(report['holes'], 1)
        png, report = S.preview('M0 0 L10 10 L10 0 L0 10 Z')
        self.assertFalse(report['valid'])
        self.assertGreater(len(png), 1000)


if __name__ == '__main__':
    unittest.main()
