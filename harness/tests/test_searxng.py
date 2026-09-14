from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.research import ResearchTool, ResearchError
from server.searxng import search


class SearxngTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handle):
        original = httpx.AsyncClient
        return patch('server.searxng.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw))

    async def test_search_uses_service_json_api_and_filters_unsafe_results(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'results': [
                {'url': 'http://127.0.0.1/private', 'title': 'Blocked'},
                {'url': 'https://example.com/drawing', 'title': 'Drawing', 'content': 'Dimensions &amp; tolerances', 'engine': 'bing'},
                {'url': 'https://example.com/drawing', 'title': 'Duplicate'}], 'unresponsive_engines': [['brave', 'timeout']]})
        tool = ResearchTool({'enabled': True, 'searxng_url': 'http://searxng:8080'})
        with self.transport(handle), patch('server.browser_research.browser_search', side_effect=AssertionError('Browser search must not be used')):
            result = await tool.search('M3 & M4')
        self.assertEqual(requests[0].url.host, 'searxng')
        self.assertEqual(requests[0].url.params['q'], 'M3 & M4')
        self.assertEqual(requests[0].url.params['format'], 'json')
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['text'], 'Dimensions & tolerances')
        self.assertFalse(result['sources'][0]['opened'])
        self.assertEqual(result['provider'], 'searxng')
        self.assertEqual(result['warnings'][0]['code'], 'engine_unavailable')

    async def test_an_empty_answer_from_rate_limited_engines_is_retried_once_and_explained(self):
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json={'results': [], 'unresponsive_engines': [['google cse', 'Too many requests'], ['brave', 'timeout']]})
            return httpx.Response(200, json={'results': [{'url': 'https://example.com/drawing', 'title': 'Drawing', 'content': '58 x 49'}]})
        tool = ResearchTool({'enabled': True, 'searxng_url': 'http://searxng:8080'})
        with self.transport(handle), patch('server.searxng.asyncio.sleep') as sleep:
            result = await tool.search('pi 4 mounting holes')
        self.assertEqual(len(calls), 2)
        sleep.assert_awaited_once()
        self.assertEqual(result['sources'][0]['text'], '58 x 49')
        calls.clear()
        def exhausted(request):
            calls.append(request)
            return httpx.Response(200, json={'results': [], 'unresponsive_engines': [['google cse', 'Too many requests']]})
        with self.transport(exhausted), patch('server.searxng.asyncio.sleep'):
            with self.assertRaises(ResearchError) as caught:
                await tool.search('pi 4 mounting holes')
        self.assertEqual(len(calls), 2)
        self.assertIn('google cse: Too many requests', str(caught.exception))
        self.assertEqual(caught.exception.code, 'no_results')

    async def test_image_search_uses_images_category(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'results': [{'url': 'https://example.com/page', 'img_src': 'https://example.com/image.png'}]})
        with self.transport(handle):
            _, rows, _ = await search('http://searxng:8080', 'drawing', images=True)
        self.assertEqual(requests[0].url.params['categories'], 'images')
        self.assertEqual(rows[0]['url'], 'https://example.com/image.png')
        self.assertEqual(rows[0]['page'], 'https://example.com/page')

    async def test_unavailable_malformed_and_oversized_responses_are_actionable_errors(self):
        for response in (httpx.Response(403), httpx.Response(200, text='not json'), httpx.Response(200, json=[]),
                         httpx.Response(200, content=b'x' * (2 * 1024 * 1024 + 1)), httpx.Response(302, headers={'location': 'http://other/'})):
            with self.transport(lambda request: response), self.assertRaises(ResearchError):
                await search('http://searxng:8080', 'drawing')

    async def test_disabled_search_does_not_contact_service(self):
        with self.transport(lambda request: self.fail('Unexpected network request')), self.assertRaises(ResearchError):
            await ResearchTool({'enabled': False, 'searxng_url': 'http://searxng:8080'}).search('drawing')
