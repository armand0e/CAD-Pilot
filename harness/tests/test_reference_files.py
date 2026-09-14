"""Reference files beyond STEP/STL: sniffed downloads, DXF/SVG in the project, research pointing at import_reference."""
import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import projects, research  # noqa: E402
from server.projects import reference_name, sniff_reference  # noqa: E402
from server.research import ResearchError, ResearchTool, textual  # noqa: E402
from server.source_workspace import network_hint  # noqa: E402
from server.pi_agent import parallel_safe  # noqa: E402
from server.operations import TOOL_DEFINITIONS  # noqa: E402
from server.workspace_tools import TOOLS as WORKSPACE_TOOLS  # noqa: E402

DXF = b'  0\r\nSECTION\r\n  2\r\nHEADER\r\n  9\r\n$ACADVER\r\n  1\r\nAC1021\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n'
STEP = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n'
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
BINARY_STL = b'\0' * 80 + struct.pack('<I', 1) + b'\0' * 50


class SniffTests(unittest.TestCase):
    def test_cad_file_types_are_recognised_by_content(self):
        self.assertEqual(sniff_reference(DXF), 'dxf')
        self.assertEqual(sniff_reference(STEP), 'step')
        self.assertEqual(sniff_reference(SVG), 'svg')
        self.assertEqual(sniff_reference(b'solid cube\n facet normal 0 0 1\n'), 'stl')
        self.assertEqual(sniff_reference(BINARY_STL), 'stl')
        self.assertIsNone(sniff_reference(b'%PDF-1.7 ...'))
        self.assertIsNone(sniff_reference(b'<!doctype html><html><body>Download</body></html>'))
        self.assertIsNone(sniff_reference(b''))

    def test_reference_name_prefers_a_cad_file_name_then_the_document_number(self):
        redirected = 'https://assets.example/categories/545/documents/RP-008342-DS-1-mechanical-drawing.dxf'
        self.assertEqual(reference_name('https://pip.example/documents/RP-008342-DS', redirected, DXF), 'RP-008342-DS-1-mechanical-drawing.dxf')
        self.assertEqual(reference_name('https://pip.example/documents/RP-008342-DS', 'https://cdn.example/dl?id=9', DXF), 'RP-008342-DS.dxf')
        self.assertEqual(reference_name('https://x.example/board.STEP', 'https://x.example/board.STEP', STEP), 'board.STEP')
        with self.assertRaisesRegex(ValueError, 'did not return a STEP'):
            reference_name('https://x.example/page', 'https://x.example/page', b'<html>nope</html>')

    def test_textual_downloads_are_decoded_and_binaries_refused(self):
        self.assertEqual(textual(b'58 x 49 mm\n', 'text/plain'), '58 x 49 mm\n')
        self.assertIsNone(textual(bytes(range(256)) * 10, 'application/octet-stream'))
        self.assertIsNone(textual(b'GIF89a', 'image/gif'))

    def test_network_attempts_in_the_sandbox_get_a_pointer(self):
        self.assertIn('import_reference', network_hint('/bin/bash: line 1: curl: command not found'))
        self.assertIn('research tool', network_hint('urllib.error.URLError: <urlopen error [Errno -3] Temporary failure in name resolution>'))
        self.assertEqual(network_hint('Traceback: ValueError: bad radius'), '')


class ParallelToolTests(unittest.TestCase):
    def test_questions_and_lookups_run_beside_a_research_delegation(self):
        marked = parallel_safe(TOOL_DEFINITIONS + WORKSPACE_TOOLS)
        modes = {t['function']['name']: t.get('execution') for t in marked}
        self.assertEqual(modes['ask_question'], 'parallel')
        self.assertEqual(modes['view_image'], 'parallel')
        self.assertEqual(modes['research'], 'parallel')
        for name in ('cad_build', 'create_body', 'spec_update', 'create_path_body'):
            if name in modes:
                self.assertIsNone(modes[name], name)
        self.assertTrue(all('execution' not in t for t in TOOL_DEFINITIONS))  # the shared list is left untouched


class ProjectReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _project(self):
        return projects.Project.create(Path(self.tmp.name), 'freecad')

    def test_dxf_and_svg_references_are_stored_and_mismatches_refused(self):
        project = self._project()
        self.assertEqual(project.add_reference('board.dxf', DXF), 'board.dxf')
        self.assertEqual(project.add_reference('logo.svg', SVG), 'logo.svg')
        with self.assertRaisesRegex(ValueError, 'Not a DXF'):
            project.add_reference('fake.dxf', b'<html>no</html>')
        with self.assertRaisesRegex(ValueError, 'ISO-10303'):
            project.add_reference('fake.step', DXF)
        with self.assertRaisesRegex(ValueError, 'plain'):
            project.add_reference('notes.txt', b'x')
        self.assertEqual(project.references(), ['board.dxf', 'logo.svg'])


class ResearchDownloadTests(unittest.TestCase):
    def _fetch(self, url, browser_error, body, content_type):
        tool = ResearchTool({'enabled': True})

        async def failing(url):
            raise browser_error

        async def download(url, **kwargs):
            return url, content_type, body
        with patch('server.browser_research.browser_read', failing), patch.object(research, 'fetch_public', download):
            return asyncio.run(tool.fetch(url))

    def test_a_dxf_download_points_the_model_at_import_reference(self):
        with self.assertRaises(ResearchError) as caught:
            self._fetch('https://pip.example/documents/RP-008342-DS', ResearchError('Website navigation failed or timed out'), DXF, 'image/vnd.dxf')
        self.assertIn('import_reference', str(caught.exception))
        self.assertIn('DXF', str(caught.exception))

    def test_a_plain_text_download_reads_as_a_page(self):
        document = self._fetch('https://pip.example/notes', ResearchError('Website navigation failed or timed out'), b'Hole pitch 58 x 49 mm\n', 'text/plain')
        self.assertEqual(document['kind'], 'page')
        self.assertIn('58 x 49', document['text'])

    def test_other_browser_errors_still_surface(self):
        with self.assertRaisesRegex(ResearchError, 'HTTP 404'):
            self._fetch('https://pip.example/missing', ResearchError('Website returned HTTP 404'), b'', 'text/html')


if __name__ == '__main__':
    unittest.main()
