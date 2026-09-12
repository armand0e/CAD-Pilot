"""Ephemeral authenticated CONNECT proxy: Chromium TLS with pinned public-only egress.

Browser request interception alone does not prevent DNS rebinding. Resolve and
validate every proxy connection, then connect to that literal IP, never re-resolve.
No request bodies, query strings or proxy credentials are logged.
"""
import asyncio
import base64
import secrets
from urllib.parse import urlsplit

from .research import ResearchError, public_url, resolve_public


class PublicBrowserProxy:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.clients = set()
        self.sockets = set()
        self.bytes = 0
        self.connections = 0
        self.last_error = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=16384)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    @property
    def options(self):
        return {'server': f'http://127.0.0.1:{self.port}', 'username': 'cadpilot',
                'password': self.token, 'bypass': '<-loopback>'}

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.clients):
            task.cancel()
        if self.clients:
            await asyncio.gather(*list(self.clients), return_exceptions=True)
        for writer in list(self.sockets):
            writer.close()

    async def pipe(self, reader, writer):
        while block := await reader.read(65536):
            self.bytes += len(block)
            if self.bytes > 32 * 1024**2:
                raise ResearchError('Browser research byte budget exceeded')
            writer.write(block)
            await writer.drain()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        self.sockets.add(writer)
        upstream, pumps = None, []
        try:
            async with asyncio.timeout(40):
                head = (await reader.readuntil(b'\r\n\r\n')).decode('iso-8859-1')
                lines = head.split('\r\n')
                method, target, protocol = lines[0].split(' ')
                headers = dict(line.split(':', 1) for line in lines[1:] if ':' in line)
                headers = {key.lower(): value.strip() for key, value in headers.items()}
                expected = 'Basic ' + base64.b64encode(('cadpilot:' + self.token).encode()).decode()
                if not secrets.compare_digest(headers.get('proxy-authorization', ''), expected):
                    writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="CADPilot"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                    await writer.drain()
                    return
                if method == 'CONNECT':
                    url = public_url('https://' + target + '/')
                    parts, port = urlsplit(url), 443
                elif method in ('GET', 'HEAD'):
                    parts = urlsplit(public_url(target))
                    if parts.scheme != 'http':
                        raise ResearchError('HTTPS requires CONNECT')
                    port = 80
                else:
                    raise ResearchError('Read-only browser proxy refuses this method')
                self.connections += 1
                if self.connections > 100:
                    raise ResearchError('Browser connection budget exceeded')
                address = await resolve_public(parts.hostname, port)
                upstream_reader, upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 8)
                self.sockets.add(upstream)
                if method == 'CONNECT':
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    await writer.drain()
                else:
                    # Discard proxy authentication and caller-selected Host on clear HTTP.
                    path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
                    clean = [f'{method} {path} HTTP/1.1', f'Host: {parts.netloc}']
                    clean += [f'{k}: {v}' for k, v in headers.items() if k not in ('proxy-authorization', 'proxy-connection', 'host', 'connection', 'content-length', 'transfer-encoding')]
                    upstream.write(('\r\n'.join(clean) + '\r\nConnection: close\r\n\r\n').encode('iso-8859-1'))
                    await upstream.drain()
                pumps = [asyncio.create_task(self.pipe(reader, upstream)), asyncio.create_task(self.pipe(upstream_reader, writer))]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for pump in done:
                    pump.result()
        except ResearchError as error:
            self.last_error = str(error)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            for connection in (writer, upstream):
                if connection:
                    connection.close()
                    self.sockets.discard(connection)
            self.clients.discard(task)
