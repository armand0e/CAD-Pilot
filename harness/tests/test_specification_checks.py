"""check_verification: prose feature labels never block a measurement; identifier features must match the measured body."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.specification import check_verification  # noqa: E402


def evidence(subject):
    return {'ev1': {'revision': 'r1', 'query': 'objects', 'object': subject, 'objects': [{'id': subject, 'bounds_mm': [30.0, 30.0, 14.0]}]}}


def row(features):
    return {'id': 'body-size', 'features': features,
            'verification': {'kind': 'measurement', 'evidence': 'ev1', 'field': '/objects/0/bounds_mm/0', 'expected': 30, 'tolerance': 0.1}}


class FeatureLinkingTests(unittest.TestCase):
    def test_prose_features_verify_against_any_measured_body(self):
        for subject in ('CADPilotResult', 'Part1'):
            result = check_verification(row(['dome top', 'six scallops']), evidence(subject), 'r1', lambda p: Path(p))
            self.assertEqual(result['subjects'], [subject])
            self.assertAlmostEqual(result['actual'], 30.0)

    def test_the_whole_source_result_covers_identifier_features_too(self):
        result = check_verification(row(['Part2', 'Part2:Face3']), evidence('CADPilotResult'), 'r1', lambda p: Path(p))
        self.assertEqual(result['subjects'], ['CADPilotResult'])

    def test_an_identifier_feature_must_name_the_measured_body(self):
        with self.assertRaisesRegex(ValueError, 'Link the requirement features'):
            check_verification(row(['Part2']), evidence('Part1'), 'r1', lambda p: Path(p))
        check_verification(row(['Part1:Face3']), evidence('Part1'), 'r1', lambda p: Path(p))
        check_verification(row(['lid rim Part1 (r1)']), evidence('Part1'), 'r1', lambda p: Path(p))


if __name__ == '__main__':
    unittest.main()
