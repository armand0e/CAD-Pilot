import asyncio
import hashlib
import io
import json
from pathlib import Path
import socket
import shlex
import tempfile
import unittest
import zipfile

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import Response
from websockets.asyncio.client import connect

from server.portal import create_app, ROOT
from server.relay import serve_worker


class PortalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'web').symlink_to(ROOT / 'web', target_is_directory=True)
        self.app = create_app(root, origin='http://127.0.0.1', image='example.test/cadpilot:tested')
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        self.port = listener.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', lifespan='off'))
        self.serving = asyncio.create_task(self.server.serve(sockets=[listener]))
        while not self.server.started:
            await asyncio.sleep(.01)
        self.clients, self.workers = [], []
        self.url = f'http://127.0.0.1:{self.port}'

    async def asyncTearDown(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await asyncio.gather(*(client.aclose() for client in self.clients))
        self.server.should_exit = True
        await self.serving

    async def account(self, name):
        client = httpx.AsyncClient(base_url=self.url, timeout=30)
        self.clients.append(client)
        response = await client.post('/api/auth/register', json={'username': name, 'password': 'test-password-long-enough'})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    async def pair(self, client, marker, *, relay_version=2):
        response = await client.post('/api/onboarding/bundle')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            import yaml
            compose = yaml.safe_load(archive.read('compose.yaml'))
            if compose['services']['cad'].get('build'):
                for name in ('harness/pi/runtime.mjs', 'harness/pi/package.json', 'harness/pi/package-lock.json', 'harness/server/pi_agent.py'):
                    self.assertIn('source/' + name, archive.namelist())
            identity = compose['services']['cad']['environment']['CADPILOT_INSTANCE_ID']
            token = archive.read('pairing.env').decode().strip().split('=', 1)[1]
            self.assertNotIn('ports', compose['services']['cad'])
        cad = FastAPI()
        @cad.get('/api/status')
        async def status():
            return {'planner': {'ok': True}, 'missing_tools': []}
        @cad.get('/api/apps')
        async def apps():
            return {'apps': [{'id': 'freecad'}]}
        @cad.get('/api/projects')
        async def projects():
            return {'owner': marker}
        @cad.get('/api/projects/test/binary')
        async def binary():
            return Response(b'\xff\x00\x80a' * 5000000, media_type='application/octet-stream')
        @cad.post('/api/sessions/test/attachments')
        async def upload(request: Request):
            body = await request.body()
            return {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        @cad.websocket('/ws/view/test')
        async def view(ws: WebSocket):
            await ws.accept()
            await ws.send_bytes(b'\xff\x00viewport')
            await ws.send_text(await ws.receive_text())
            await ws.close()
        @cad.websocket('/ws/agent/large')
        async def activity(ws: WebSocket):
            await ws.accept()
            # Exceeds the production relay's 16 MiB message limit even before
            # encoding. It must still arrive as one browser-visible JSON value.
            await ws.send_json({'transcript': 'CAD 🛠\n' * 2000000})
            await ws.send_text(await ws.receive_text())
            await ws.send_bytes(b'\xff\x00\x80a' * 5000000)
            await ws.send_json({'t': 'done'})
            await ws.close()
        ready = asyncio.Event()
        async def worker():
            async with connect(f'ws://127.0.0.1:{self.port}/api/worker/connect', additional_headers={
                    'Authorization': 'Bearer ' + token, 'X-CADPilot-Instance': identity,
                    'X-CADPilot-Relay-Version': str(relay_version)}, max_size=16*1024*1024) as ws:
                message = json.loads(await ws.recv())
                self.credential = message['credential']
                await ws.send(json.dumps({'type': 'ack', 'credential': self.credential}))
                ready.set()
                await serve_worker(cad, ws, fragment_ws=relay_version >= 2)
        task = asyncio.create_task(worker())
        self.workers.append(task)
        await asyncio.wait_for(ready.wait(), 5)
        return identity, token

    async def test_accounts_pairing_isolation_http_and_websocket(self):
        alice, bob = await self.account('alice'), await self.account('bob')
        self.assertEqual((await alice.get('/')).headers['location'], '/onboarding')
        self.assertEqual((await alice.get('/api/projects')).status_code, 503)
        alice_id, token = await self.pair(alice, 'alice')
        await self.pair(bob, 'bob')
        for client in (alice, bob):
            response = await client.post('/api/onboarding/complete')
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await alice.get('/api/projects')).json(), {'owner': 'alice'})
        self.assertEqual((await bob.get('/api/projects')).json(), {'owner': 'bob'})
        self.assertEqual((await bob.delete('/api/onboarding/instance/' + alice_id)).status_code, 404)
        self.assertIsNone(self.app.state.instances.authenticate_worker(alice_id, token))
        body = b'\xff\x00a\x80' * 2000000
        uploaded = await alice.post('/api/sessions/test/attachments', content=body)
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json(), {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
        downloaded = await alice.get('/api/projects/test/binary')
        self.assertEqual(downloaded.content, b'\xff\x00\x80a' * 5000000)
        self.assertEqual(downloaded.headers['content-length'], '20000000')
        cookie = '; '.join(f'{key}={value}' for key, value in alice.cookies.items())
        async with connect(f'ws://127.0.0.1:{self.port}/ws/view/test', additional_headers={'Cookie': cookie}) as ws:
            self.assertEqual(await ws.recv(), b'\xff\x00viewport')
            await ws.send('move')
            self.assertEqual(await ws.recv(), 'move')
        self.assertEqual((await alice.delete('/api/onboarding/instance/' + alice_id)).status_code, 200)
        self.assertEqual((await alice.get('/api/projects')).status_code, 503)
        self.assertEqual((await bob.get('/api/projects')).status_code, 200)

    async def test_large_chat_snapshot_and_ws_messages_keep_boundaries_and_connection(self):
        client = await self.account('long-chat')
        await self.pair(client, 'long-chat')
        self.assertEqual((await client.post('/api/onboarding/complete')).status_code, 200)
        cookie = '; '.join(f'{key}={value}' for key, value in client.cookies.items())
        async with connect(f'ws://127.0.0.1:{self.port}/ws/agent/large', additional_headers={'Cookie': cookie},
                           max_size=64*1024*1024) as ws:
            snapshot = json.loads(await asyncio.wait_for(ws.recv(), 20))
            self.assertEqual(snapshot, {'transcript': 'CAD 🛠\n' * 2000000})
            steering = json.dumps({'t': 'intent', 'text': 'opening 🛠\n' * 100000})
            await ws.send(steering)
            self.assertEqual(await asyncio.wait_for(ws.recv(), 20), steering)
            self.assertEqual(await asyncio.wait_for(ws.recv(), 20), b'\xff\x00\x80a' * 5000000)
            self.assertEqual(json.loads(await ws.recv()), {'t': 'done'})
        self.assertEqual((await client.get('/api/projects')).json(), {'owner': 'long-chat'})

    async def test_portal_does_not_fragment_messages_to_an_older_worker(self):
        client = await self.account('older-worker')
        await self.pair(client, 'older-worker', relay_version=1)
        self.assertEqual((await client.post('/api/onboarding/complete')).status_code, 200)
        cookie = '; '.join(f'{key}={value}' for key, value in client.cookies.items())
        async with connect(f'ws://127.0.0.1:{self.port}/ws/view/test', additional_headers={'Cookie': cookie}) as ws:
            self.assertEqual(await ws.recv(), b'\xff\x00viewport')
            await ws.send('long steering message ' * 3000)
            self.assertEqual(await ws.recv(), 'long steering message ' * 3000)

    async def test_public_host_secure_login_and_expired_pairing(self):
        client = await self.account('carol')
        self.assertEqual((await client.post('/api/onboarding/bundle', headers={'Origin': 'https://evil.test'})).status_code, 403)
        pair = self.app.state.instances.pair('carol')
        with self.app.state.auth.db() as db:
            db.execute('UPDATE instances SET pairing_expires=0 WHERE id=?', (pair['id'],))
        self.assertIsNone(self.app.state.instances.authenticate_worker(pair['id'], pair['token']))
        secure = create_app(Path(self.temp.name), origin='https://cad.armand0e.com')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(secure), base_url='https://cad.armand0e.com') as https:
            result = await https.post('/api/auth/login', json={'username': 'carol', 'password': 'test-password-long-enough'})
            self.assertIn('Secure', result.headers['set-cookie'])
            self.assertEqual((await https.get('/')).headers['location'], '/onboarding')
            self.assertEqual((await https.get('/healthz', headers={'Host': 'evil.test'})).status_code, 400)

    async def test_github_command_is_private_account_owned_and_revokes_old_pairing(self):
        alice, bob = await self.account('github-alice'), await self.account('github-bob')
        result = await alice.post('/api/onboarding/command')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['cache-control'], 'no-store')
        data = result.json()
        self.assertEqual(set(data['commands']), {'windows', 'linux', 'macos'})
        self.assertEqual(data['commands']['linux'], data['command'])
        self.assertIn('wsl.exe --exec bash -s --', data['commands']['windows'])
        self.assertEqual(data['repository'], 'https://github.com/armand0e/CAD-Pilot')
        origin, identity, token = shlex.split(data['command'])[-3:]
        self.assertEqual(origin, 'http://127.0.0.1')
        self.assertEqual(self.app.state.instances.authenticate_worker(identity, token)['owner'], 'github-alice')
        self.assertIsNone((await bob.get('/api/onboarding')).json()['instance'])
        self.assertNotIn(token, (await alice.get('/api/onboarding')).text)
        updated = await alice.post('/api/onboarding/command')
        new_identity, new_token = shlex.split(updated.json()['command'])[-2:]
        self.assertEqual(identity, new_identity)
        self.assertIsNone(self.app.state.instances.authenticate_worker(identity, token))
        self.assertEqual(self.app.state.instances.authenticate_worker(identity, new_token)['owner'], 'github-alice')
        self.assertEqual((await alice.post('/api/onboarding/command', headers={'Origin': 'https://evil.test'})).status_code, 403)
        async with httpx.AsyncClient(base_url=self.url) as anonymous:
            self.assertEqual((await anonymous.post('/api/onboarding/command')).status_code, 401)
