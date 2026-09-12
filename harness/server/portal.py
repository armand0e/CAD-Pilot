"""Public account/onboarding app. CAD requests are routed only to an owned runtime."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from . import auth
from .bundles import runtime_bundle
from .distribution import REPOSITORY_URL, connection_commands
from .instances import Instances
from .relay import Disconnected, PortalConnection, allowed_path
from .security import LocalWorkspaceSecurity

ROOT = Path(__file__).resolve().parents[1]


def create_app(root=ROOT, origin=None, image=None):
    origin = (origin if origin is not None else os.environ.get('CADPILOT_PUBLIC_ORIGIN', 'https://cad.armand0e.com')).rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('CADPILOT_PUBLIC_ORIGIN must be an http(s) origin')
    runtime_image = image if image is not None else os.environ.get('CADPILOT_RUNTIME_IMAGE', '')
    app = FastAPI(title='CADPilot accounts')
    store = auth.install(app, root, registration=True, public_origin=origin, public_paths=('/api/worker/connect',))
    instances = Instances(store)
    connections = {}
    app.state.instances, app.state.connections, app.state.auth = instances, connections, store
    app.add_middleware(LocalWorkspaceSecurity)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname, 'localhost', '127.0.0.1', '[::1]'])
    app.mount('/static', StaticFiles(directory=root / 'web'), name='static')

    def owned(scope):
        return instances.get(scope['cadpilot_user'])

    def connection(scope):
        instance = owned(scope)
        return connections.get(instance['id']) if instance else None

    async def probe(link, path):
        events = []
        received = False
        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': b''}
            await asyncio.Future()
        async def send(event):
            events.append(event)
        async with asyncio.timeout(20):
            await link.forward({'type': 'http', 'method': 'GET', 'path': path, 'query_string': b'', 'headers': []}, receive, send)
        if not events or events[0].get('status') != 200:
            raise ValueError('CAD computer did not answer its setup check')
        return json.loads(b''.join(e.get('body', b'') for e in events))

    @app.get('/')
    async def index(request: Request):
        instance = owned(request.scope)
        if not instance or not instance['completed'] or not connection(request.scope):
            return RedirectResponse('/onboarding', 303)
        return FileResponse(root / 'web/index.html')

    @app.get('/onboarding')
    async def onboarding():
        return FileResponse(root / 'web/onboarding.html')

    @app.get('/api/onboarding')
    async def state(request: Request):
        instance = owned(request.scope)
        return {'origin': origin, 'image': runtime_image or None, 'instance': instance,
                'online': bool(connection(request.scope)), 'build_required': not bool(runtime_image),
                'repository': REPOSITORY_URL}

    async def new_pair(request):
        pair = await asyncio.to_thread(instances.pair, request.scope['cadpilot_user'])
        old = connections.pop(pair['id'], None)
        if old:
            old.close()
            await old.ws.close(code=4403)
        return pair

    @app.post('/api/onboarding/command')
    async def setup_command(request: Request):
        pair = await new_pair(request)
        commands = connection_commands(pair, origin)
        return JSONResponse({'command': commands['linux'], 'commands': commands, 'expires': pair['expires'],
                             'repository': REPOSITORY_URL})

    @app.post('/api/onboarding/bundle')
    async def bundle(request: Request):
        pair = await new_pair(request)
        archive = await asyncio.to_thread(runtime_bundle, pair, origin, runtime_image)
        return Response(archive, media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="cadpilot-setup.zip"'})

    @app.post('/api/onboarding/complete')
    async def complete(request: Request):
        link = connection(request.scope)
        if not link:
            raise HTTPException(409, 'Start your CAD computer first')
        try:
            status, apps = await asyncio.gather(probe(link, '/api/status'), probe(link, '/api/apps'))
        except (TimeoutError, Disconnected, ValueError):
            raise HTTPException(409, 'Your CAD computer is not ready yet; check its Docker logs') from None
        if not status['planner']['ok']:
            raise HTTPException(409, 'The selected model is not reachable from your CAD computer')
        if status['missing_tools'] or not apps['apps']:
            raise HTTPException(409, 'CAD applications or required system tools are missing')
        instances.complete(request.scope['cadpilot_user'])
        return {'ready': True}

    @app.delete('/api/onboarding/instance/{identity}')
    async def disconnect(identity: str, request: Request):
        instances.revoke(request.scope['cadpilot_user'], identity)
        link = connections.pop(identity, None)
        if link:
            link.close()
            await link.ws.close(code=4403)
        return {'disconnected': True}

    @app.websocket('/api/worker/connect')
    async def worker(ws: WebSocket):
        identity = ws.headers.get('x-cadpilot-instance', '')
        token = ws.headers.get('authorization', '').removeprefix('Bearer ')
        result = await asyncio.to_thread(instances.authenticate_worker, identity, token)
        if not result:
            return await ws.close(code=4403)
        credential = result['credential'] or token
        await ws.accept()
        link = PortalConnection(ws)
        previous = connections.get(identity)
        if previous:
            previous.close()
            await previous.ws.close(code=1012)
        connections[identity] = link
        try:
            await link.send({'type': 'paired', 'credential': result['credential']})
            while True:
                packet = await asyncio.wait_for(ws.receive_json(), 50)
                if packet.get('type') == 'ack':
                    if packet.get('credential') != credential:
                        break
                    await asyncio.to_thread(instances.acknowledge, identity, credential)
                elif packet.get('type') == 'ping':
                    valid = await asyncio.to_thread(instances.authenticate_worker, identity, credential)
                    if not valid:
                        break
                    await link.send({'type': 'pong'})
                elif packet.get('type') in ('event', 'end'):
                    await link.deliver(packet)
                else:
                    break
        except (WebSocketDisconnect, TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            link.close()
            if connections.get(identity) is link:
                connections.pop(identity, None)
            with contextlib.suppress(Exception):
                await ws.close()

    class Dispatch:
        async def __call__(self, scope, receive, send):
            path = scope['path']
            if not allowed_path(path):
                response = JSONResponse({'detail': 'Unknown CAD route'}, 404)
                return await response(scope, receive, send)
            instance = owned(scope)
            setup_route = path in ('/api/settings', '/api/status', '/api/apps')
            link = connection(scope)
            if not link or (not setup_route and not instance['completed']):
                if scope['type'] == 'websocket':
                    return await send({'type': 'websocket.close', 'code': 4410})
                return await JSONResponse({'detail': 'Connect your CAD computer in onboarding', 'onboarding': True}, 503)(scope, receive, send)
            started = False
            async def output(event):
                nonlocal started
                started = True
                await send(event)
            try:
                await link.forward(scope, receive, output)
            except Disconnected:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 4410})
                elif not started:
                    await JSONResponse({'detail': 'CAD computer disconnected', 'onboarding': True}, 503)(scope, receive, send)
                else:
                    await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    # A final ASGI route preserves streaming HTTP uploads/downloads and WS bytes.
    from starlette.routing import Mount
    app.router.routes.append(Mount('/', app=Dispatch()))
    return app


app = create_app()
