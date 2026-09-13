"""Model restarts refresh autodetected capacity without overriding user limits."""
import copy
import unittest
from unittest.mock import patch

import httpx

from server import app as web


class EndpointHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_context_follows_model_restart_and_explicit_window_is_preserved(self):
        config = copy.deepcopy(web.CONFIG)
        settings = copy.deepcopy(web.SETTINGS)
        active = next(m for m in settings['models'] if m['name'] == settings['active_model'])
        active['context_window'] = None
        config['planner']['max_model_len'] = 65536
        served_window = 131072

        def respond(request):
            return httpx.Response(200, json={'data': [{'id': config['planner']['model'], 'max_model_len': served_window}]})

        client_class = httpx.AsyncClient
        with patch.object(web, 'CONFIG', config), patch.object(web, 'SETTINGS', settings), \
                patch.object(web, '_endpoint_cache', {'ts': 0}), \
                patch('httpx.AsyncClient', side_effect=lambda **kw: client_class(transport=httpx.MockTransport(respond), **kw)):
            await web._endpoint_health()
            self.assertEqual(config['planner']['max_model_len'], 131072)
            web._endpoint_cache['ts'] = 0
            served_window = 262144
            await web._endpoint_health()
            self.assertEqual(config['planner']['max_model_len'], 262144)
            active['context_window'] = 65536
            config['planner']['max_model_len'] = 65536
            web._endpoint_cache['ts'] = 0
            await web._endpoint_health()
            self.assertEqual(config['planner']['max_model_len'], 65536)
