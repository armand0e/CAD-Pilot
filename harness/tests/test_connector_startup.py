import unittest
import errno
import socket
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from server.connector import validate_origin, connection_failure


class ConnectorStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_portal_is_rejected_before_background_task_starts(self):
        from server.app import start_connector
        with patch.dict('os.environ', {'CADPILOT_PORTAL_URL': 'http://remote.example'}):
            with self.assertRaisesRegex(ValueError, 'require HTTPS'):
                await start_connector()
        for url in ('https://', 'https://:secret@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError):
                validate_origin(url)
        self.assertEqual(validate_origin('https://cad.armand0e.com').hostname, 'cad.armand0e.com')
        self.assertEqual(validate_origin('http://host.docker.internal:7804').port, 7804)

    async def test_worker_health_requires_portal_connection(self):
        from server.auth import install
        app = FastAPI()
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'CADPILOT_AUTH_ENABLED': '0'}):
            install(app, Path(root))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1') as client:
                self.assertEqual((await client.get('/healthz')).json(), {'ok': True})
                for state in ('connecting', 'dns_error', 'pairing_rejected', 'disconnected'):
                    app.state.connector_status = {'state': state, 'detail': 'retrying'}
                    response = await client.get('/healthz')
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()['connector']['state'], state)
                app.state.connector_status = {'state': 'connected', 'portal': 'cad.example'}
                self.assertEqual((await client.get('/healthz')).status_code, 200)

    def test_network_errors_explain_recovery_without_logging_credentials(self):
        from websockets.exceptions import InvalidStatus
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        self.assertEqual(connection_failure(socket.gaierror(-2, 'private-url-token'))[0], 'dns_error')
        self.assertEqual(connection_failure(OSError(errno.ENETUNREACH, 'private-url-token'))[0], 'network_unreachable')
        self.assertEqual(connection_failure(InvalidStatus(Response(403, 'Forbidden', Headers(), b'')))[0], 'pairing_rejected')
        self.assertEqual(connection_failure(InvalidStatus(Response(403, 'Forbidden', Headers(), b'error code: 1010')))[0], 'proxy_rejected')
        self.assertNotIn('private-url-token', str(connection_failure(RuntimeError('private-url-token'))))
