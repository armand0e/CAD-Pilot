import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from server.agent import AgentRunner


class ModelTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        return AgentRunner(SimpleNamespace(app={'name': 'FreeCAD'}), {'agent': {}, 'planner': {}, 'policy': {}})

    async def test_native_timeout_is_transport_configuration_not_model_payload(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]})
        client = httpx.AsyncClient
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)):
            result = await self.runner()._chat({'base_url': 'http://test/v1', 'model': 'test'}, [], request_timeout_s=180, max_tokens=8192)
        self.assertEqual(result, '{}')
        self.assertEqual(requests[0].extensions['timeout']['read'], 180)
        self.assertNotIn('request_timeout_s', json.loads(requests[0].content))

    async def test_read_timeout_does_not_restart_same_long_generation_three_times(self):
        requests = []
        def handle(request):
            requests.append(request)
            raise httpx.ReadTimeout('still decoding', request=request)
        client = httpx.AsyncClient
        with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)):
            with self.assertRaisesRegex(RuntimeError, 'exceeded 180 seconds'):
                await self.runner()._chat({'base_url': 'http://test/v1', 'model': 'test'}, [], request_timeout_s=180)
        self.assertEqual(len(requests), 1)

    async def test_default_and_zero_are_unlimited_and_explicit_limits_are_not_clamped(self):
        client = httpx.AsyncClient
        for limit in (None, 0, 900):
            requests = []
            def handle(request):
                requests.append(request)
                return httpx.Response(200, json={'choices': [{'message': {'content': 'Ready'}, 'finish_reason': 'stop'}]})
            with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kw: client(transport=httpx.MockTransport(handle), **kw)):
                result = await self.runner()._chat_tools({'base_url': 'http://test/v1', 'model': 'test'}, [], [], request_timeout_s=limit)
            self.assertEqual(result['content'], 'Ready')
            self.assertEqual(requests[0].extensions['timeout']['read'], limit or None)
            self.assertEqual(requests[0].extensions['timeout']['connect'], 5)

    async def test_slow_generation_finishes_without_deadline_and_remains_cancellable(self):
        class SlowStream(httpx.AsyncByteStream):
            def __init__(self):
                self.started = asyncio.Event()
                self.closed = False
            async def __aiter__(self):
                self.started.set()
                await asyncio.sleep(.05)
                yield b'data: {"choices":[{"delta":{"content":"Ready"},"finish_reason":"stop"}]}\n\n'
            async def aclose(self):
                self.closed = True
        client = httpx.AsyncClient
        endpoint = {'base_url': 'http://test/v1', 'model': 'test'}
        for limit, cancel in ((None, False), (.01, False), (None, True)):
            stream = SlowStream()
            with patch('server.agent.httpx.AsyncClient', side_effect=lambda **kw: client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)), **kw)):
                task = asyncio.create_task(self.runner()._chat_tools(endpoint, [], [], request_timeout_s=limit))
                await stream.started.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                elif limit:
                    with self.assertRaisesRegex(RuntimeError, 'exceeded 0.01 seconds'):
                        await task
                else:
                    self.assertEqual((await task)['content'], 'Ready')
            self.assertTrue(stream.closed)

    def test_invalid_deadlines_are_rejected(self):
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.runner()._request_timeout(value)
