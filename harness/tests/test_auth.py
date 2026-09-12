import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import auth
from server.security import LocalWorkspaceSecurity


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = auth.AuthStore(self.root / 'state/auth.sqlite3')
        self.store.set_owner('admin', 'test-password-long-enough')
        app = FastAPI()
        with patch.dict(os.environ, {'CADPILOT_AUTH_ENABLED': '1'}):
            auth.install(app, self.root)
        app.add_middleware(LocalWorkspaceSecurity)

        @app.get('/api/private')
        async def private():
            return {'secret': True}

        @app.websocket('/ws/test')
        async def socket(ws: WebSocket):
            await ws.accept()
            try:
                while True:
                    await ws.send_text(await ws.receive_text())
            except WebSocketDisconnect:
                await ws.close(code=4401)

        self.client = TestClient(app, base_url='http://127.0.0.1')
        self.addCleanup(self.client.close)

    def login(self, password='test-password-long-enough'):
        return self.client.post('/api/auth/login', json={'username': 'admin', 'password': password})

    def test_login_cookies_and_api_revocation(self):
        self.assertEqual(self.client.get('/api/private').status_code, 401)
        self.assertEqual(self.client.get('/', follow_redirects=False).headers['location'], '/login')
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        self.assertEqual(self.login('wrong').status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        for attribute in ('HttpOnly', 'SameSite=strict', 'Max-Age=43200'):
            self.assertIn(attribute, response.headers['set-cookie'])
        token = self.client.cookies[auth.COOKIE]
        self.assertEqual(self.client.get('/api/private').status_code, 200)
        self.assertNotIn(token.encode(), self.store.path.read_bytes())
        self.assertNotIn(b'test-password-long-enough', self.store.path.read_bytes())
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertIsNone(self.store.user(token))
        self.assertEqual(self.client.get('/api/private').status_code, 401)

    def test_https_cookie_and_expiry(self):
        self.client.base_url = 'https://127.0.0.1'
        response = self.login()
        self.assertIn('Secure', response.headers['set-cookie'])
        token = self.client.cookies[auth.COOKIE]
        with patch('server.auth.time.time', return_value=time.time() + auth.SESSION_SECONDS + 1):
            self.assertIsNone(self.store.user(token))

    def test_no_cross_origin_login_logout_or_websocket(self):
        self.assertEqual(self.client.post('/api/auth/login', headers={'Origin': 'https://evil.example'}, json={}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code, 403)
        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect('ws://127.0.0.1/ws/test', headers={'Origin': 'https://evil.example'}):
                pass
        self.assertEqual(error.exception.code, 4403)

    def test_websocket_requires_login_and_rechecks_before_commands(self):
        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect('ws://127.0.0.1/ws/test'):
                pass
        self.assertEqual(error.exception.code, 4401)
        self.login()
        with self.client.websocket_connect('ws://127.0.0.1/ws/test') as socket:
            socket.send_text('hello')
            self.assertEqual(socket.receive_text(), 'hello')
            self.client.post('/api/auth/logout')
            socket.send_text('must not execute')
            with self.assertRaises(WebSocketDisconnect) as error:
                socket.receive_text()
            self.assertEqual(error.exception.code, 4401)

    def test_login_rate_limit_and_password_reset(self):
        self.login()
        token = self.client.cookies[auth.COOKIE]
        for _ in range(10):
            self.assertEqual(self.login('wrong').status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        self.store.set_owner('admin', 'a-new-long-password')
        self.assertIsNone(self.store.user(token))
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login('a-new-long-password').status_code, 200)

    def test_authentication_cannot_start_without_an_owner(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'CADPILOT_AUTH_ENABLED': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'No owner configured'):
                auth.install(FastAPI(), Path(root))
