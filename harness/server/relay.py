"""Multiplex bounded ASGI exchanges over an authenticated, outbound WebSocket.

The transport has no network proxy command: it serves only the paired CAD API.
Long model turns have no transport deadline; heartbeats detect lost connections.
"""
import asyncio
import base64
import contextlib
import json
import uuid

CHUNK = 65536
MAX_CHANNELS = 32


def allowed_path(path):
    return path in ('/api/status', '/api/apps', '/api/settings', '/api/sessions', '/api/projects') or path.startswith(('/api/sessions/', '/api/projects/', '/ws/view/', '/ws/agent/'))


def encode(event):
    result = dict(event)
    for key in ('body', 'bytes', 'query_string'):
        if isinstance(result.get(key), bytes):
            result[key] = {'base64': base64.b64encode(result[key]).decode()}
    if 'headers' in result:
        result['headers'] = [[k.decode('latin1'), v.decode('latin1')] for k, v in result['headers'] if k.lower() not in (b'set-cookie', b'cookie', b'authorization')]
    return result


def decode(event):
    result = dict(event)
    for key in ('body', 'bytes', 'query_string'):
        if isinstance(result.get(key), dict):
            result[key] = base64.b64decode(result[key]['base64'], validate=True)
    if 'headers' in result:
        result['headers'] = [(k.encode('latin1'), v.encode('latin1')) for k, v in result['headers']]
    return result


class Disconnected(Exception):
    pass


class PortalConnection:
    def __init__(self, websocket):
        self.ws = websocket
        self.channels = {}
        self.closed = False
        self.lock = asyncio.Lock()

    async def send(self, message):
        if self.closed:
            raise Disconnected('CAD computer is offline')
        async with self.lock:
            await self.ws.send_json(message)

    def close(self):
        self.closed = True
        for queue in self.channels.values():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)

    async def deliver(self, message):
        queue = self.channels.get(message.get('id'))
        if queue:
            await queue.put(message)

    async def forward(self, scope, receive, send):
        if self.closed or len(self.channels) >= MAX_CHANNELS:
            raise Disconnected('CAD computer is unavailable or busy')
        identity = uuid.uuid4().hex
        queue = self.channels[identity] = asyncio.Queue(maxsize=64)
        forwarded = {k: scope[k] for k in ('type', 'path', 'query_string', 'method', 'subprotocols') if k in scope}
        forwarded['headers'] = [(b'host', b'localhost')] + [(k, v) for k, v in scope.get('headers', [])
            if k.lower() in (b'content-type', b'content-length', b'accept', b'range')]
        async def incoming():
            while True:
                event = await receive()
                body = event.get('body', b'')
                if body:
                    for offset in range(0, len(body), CHUNK):
                        piece = event | {'body': body[offset:offset + CHUNK], 'more_body': offset + CHUNK < len(body) or event.get('more_body', False)}
                        await self.send({'type': 'event', 'id': identity, 'event': encode(piece)})
                else:
                    await self.send({'type': 'event', 'id': identity, 'event': encode(event)})
                if event['type'] in ('http.disconnect', 'websocket.disconnect'):
                    return
        reader = None
        try:
            await self.send({'type': 'open', 'id': identity, 'scope': encode(forwarded)})
            reader = asyncio.create_task(incoming())
            while True:
                packet = await queue.get()
                if packet is None:
                    raise Disconnected('CAD computer disconnected')
                if packet['type'] == 'end':
                    return
                event = decode(packet['event'])
                await send(event)
                if event['type'] == 'websocket.close' or (event['type'] == 'http.response.body' and not event.get('more_body')):
                    return
        finally:
            self.channels.pop(identity, None)
            if reader:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.send({'type': 'cancel', 'id': identity})


async def serve_worker(app, websocket):
    channels, tasks = {}, {}
    lock = asyncio.Lock()

    async def send(packet):
        async with lock:
            await websocket.send(json.dumps(packet))

    async def run(identity, scope, queue):
        started = False
        async def output(event):
            nonlocal started
            started = True
            await send({'type': 'event', 'id': identity, 'event': encode(event)})
        try:
            scope.update(asgi={'version': '3.0'}, http_version='1.1', scheme='http' if scope['type'] == 'http' else 'ws',
                         server=('localhost', 7800), client=('127.0.0.1', 0), root_path='')
            await app(scope, queue.get, output)
        except asyncio.CancelledError:
            raise
        except Exception:
            if scope['type'] == 'http' and not started:
                await output({'type': 'http.response.start', 'status': 502, 'headers': []})
                await output({'type': 'http.response.body', 'body': b'CAD request failed'})
            elif scope['type'] == 'websocket':
                await output({'type': 'websocket.close', 'code': 1011})
        finally:
            channels.pop(identity, None)
            tasks.pop(identity, None)
            with contextlib.suppress(Exception):
                await send({'type': 'end', 'id': identity})

    async def heartbeat():
        while True:
            await asyncio.sleep(15)
            await send({'type': 'ping'})

    heart = asyncio.create_task(heartbeat())
    try:
        while True:
            packet = json.loads(await asyncio.wait_for(websocket.recv(), timeout=50))
            identity = packet.get('id')
            if packet['type'] == 'open':
                scope = decode(packet['scope'])
                if identity in tasks or len(tasks) >= MAX_CHANNELS or scope.get('type') not in ('http', 'websocket') or not allowed_path(scope.get('path', '')):
                    raise ValueError('Invalid relay channel')
                queue = channels[identity] = asyncio.Queue(maxsize=64)
                tasks[identity] = asyncio.create_task(run(identity, scope, queue))
            elif packet['type'] == 'event' and identity in channels:
                await channels[identity].put(decode(packet['event']))
            elif packet['type'] == 'cancel' and identity in tasks:
                tasks[identity].cancel()
    finally:
        remaining = list(tasks.values()) + [heart]
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
