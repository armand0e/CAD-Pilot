import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'training/curated'))
from seed_cases import ROUNDED, ENCLOSURE, feature, episodes
from server.design import validate_design, scad_source, DESIGN_SCHEMA
from server.projects import Project


class EnclosureSchemaTests(unittest.TestCase):
    def test_decoder_schema_prevents_known_invalid_feature_arities(self):
        variants = {v['properties']['kind']['enum'][0]: v['properties']
                    for v in DESIGN_SCHEMA['properties']['features']['items']['anyOf']}
        self.assertEqual(variants['rounded_box']['dimensions']['minItems'], 4)
        self.assertEqual(variants['rounded_box']['dimensions']['maxItems'], 4)
        self.assertEqual(variants['box']['inputs']['maxItems'], 0)
        for kind in ('union', 'difference', 'intersection', 'parts'):
            self.assertEqual(variants[kind]['inputs']['minItems'], 2)
            self.assertEqual(variants[kind]['position']['items']['enum'], ['0'])
            self.assertEqual(variants[kind]['rotation']['items']['enum'], [0])

    def test_all_authored_recipes_obey_contract_including_geometric_failure(self):
        for episode in episodes():
            for step in episode['steps']:
                validate_design(step['design'])

    def test_rounding_cannot_collapse_sides_or_be_a_general_fillet(self):
        for radius in (0, -1, 20, 21):
            d = copy.deepcopy(ROUNDED); d['parameters'][-1]['value'] = radius
            with self.assertRaises(ValueError):
                validate_design(d)
        d = copy.deepcopy(ROUNDED); d['features'][0]['kind'] = 'fillet'
        with self.assertRaises(ValueError):
            validate_design(d)

    def test_parts_are_explicit_final_output_not_boolean_operands(self):
        d = copy.deepcopy(ENCLOSURE)
        d['features'].append(feature('AfterLayout', 'union', inputs=('PrintLayout', 'Outer')))
        d['result'] = 'AfterLayout'
        with self.assertRaisesRegex(ValueError, 'only allowed as the final'):
            validate_design(d)

    def test_scad_rounding_and_multi_part_source_are_parametric(self):
        source = scad_source(ENCLOSURE)
        self.assertIn('offset(r=corner_radius)', source)
        self.assertIn('feature_Base(); feature_Lid();', source)
        self.assertIn('clearance', source)


class EnclosureKernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_rounded_volume_and_rotated_placement(self):
        with tempfile.TemporaryDirectory(prefix='rounded-kernel-test-') as root:
            p = Project.create(root, 'freecad')
            d = copy.deepcopy(ROUNDED)
            d['features'][0]['position'] = ['10', '20', '30']
            d['features'][0]['rotation'] = [0, 0, 90]
            result = p.commit(await p.prepare(d), None)
            g = result['geometry']
            self.assertAlmostEqual(g['volume_mm3'], (60*40 - (4-math.pi)*4**2)*8, places=5)
            for actual, expected in zip(g['bounds_mm'], (40, 60, 8)):
                self.assertAlmostEqual(actual, expected, places=5)
            for actual, expected in zip(g['min_mm'], (-30, 20, 30)):
                self.assertAlmostEqual(actual, expected, places=5)

    async def test_enclosure_is_two_parts_and_overlap_is_rejected_without_commit(self):
        with tempfile.TemporaryDirectory(prefix='enclosure-kernel-test-') as root:
            p = Project.create(root, 'freecad')
            result = p.commit(await p.prepare(ENCLOSURE), None)
            g = result['geometry']
            self.assertFalse(g['valid_solid'])
            self.assertTrue(g['valid_geometry'])
            self.assertEqual(g['solid_count'], 2)
            self.assertEqual([v['feature'] for v in g['parts']], ['Base', 'Lid'])
            d = copy.deepcopy(ENCLOSURE)
            next(v for v in d['parameters'] if v['name'] == 'layout_gap')['value'] = -20
            with self.assertRaisesRegex(ValueError, 'touch or overlap'):
                await p.prepare(d)
            self.assertEqual(p.read()['head'], 'r0001')
            self.assertFalse(list(p.path.glob('.build-*')))

    async def test_disconnected_component_is_not_allowed_inside_parts(self):
        d = copy.deepcopy(ROUNDED)
        d['features'] += [feature('Other', 'box', ('1', '1', '1'), ('100', '0', '0')),
                          feature('BrokenPart', 'union', inputs=('Rounded', 'Other')),
                          feature('Third', 'box', ('1', '1', '1'), ('200', '0', '0')),
                          feature('Layout', 'parts', inputs=('BrokenPart', 'Third'))]
        d['result'] = 'Layout'
        with tempfile.TemporaryDirectory(prefix='parts-kernel-test-') as root:
            with self.assertRaisesRegex(ValueError, 'must be one connected solid'):
                await Project.create(root, 'freecad').prepare(d)
