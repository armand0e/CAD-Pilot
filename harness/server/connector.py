"""Persistent outbound connection from a user's CAD container to their account."""
import asyncio
import json
import logging
import os
import random
import errno
import socket
from pathlib import Path
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
from .relay import serve_worker

log = logging.getLogger('cadpilot.connector')


def connection_failure(error):
    if isinstance(error, socket.gaierror):
        return 'dns_error', 'Cannot resolve the portal hostname. Check Docker DNS (CADPILOT_DNS_PRIMARY/SECONDARY).'
    if isinstance(error, OSError) and error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return 'network_unreachable', 'No network route to the portal. Check Docker networking and IPv4 DNS records.'
    if isinstance(error, InvalidStatus):
        response = error.response
        if response.headers.get('cf-mitigated') == 'challenge' or b'error code: 10' in (response.body or b''):
            return 'proxy_rejected', 'Cloudflare rejected the worker. Allow non-browser WebSocket connections to /api/worker/connect.'
        if response.status_code in (401, 403):
            return 'pairing_rejected', 'Pairing was rejected or expired. Generate and run a fresh command in onboarding.'
        return 'portal_unavailable', f'Portal returned HTTP {response.status_code}. Check the tunnel origin and WebSocket support.'
    if isinstance(error, TimeoutError):
        return 'connection_timeout', 'Portal connection timed out. Check the tunnel and Docker network access.'
    return 'disconnected', f'Portal connection unavailable ({type(error).__name__}). Check the tunnel and container network.'


def validate_origin(origin):
    origin = origin.rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError('CADPILOT_PORTAL_URL must be an http(s) origin')
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', 'host.docker.internal'):
        raise ValueError('Remote portal connections require HTTPS')
    return parsed


async def connect_runtime(app):
    origin = os.environ['CADPILOT_PORTAL_URL'].rstrip('/')
    parsed = validate_origin(origin)
    url = ('wss' if parsed.scheme == 'https' else 'ws') + '://' + parsed.netloc + '/api/worker/connect'
    identity = os.environ['CADPILOT_INSTANCE_ID']
    credentials = Path(__file__).resolve().parents[1] / 'state/worker-credential.json'
    pairing = Path(os.environ.get('CADPILOT_PAIRING_FILE', '/run/secrets/pairing_token'))
    delay = 1
    use_saved = True
    while True:
        try:
            saved = json.loads(credentials.read_text()) if credentials.exists() else {}
            token = saved.get('token') if use_saved and saved.get('instance') == identity and saved.get('origin') == origin else None
            token = token or os.environ.get('CADPILOT_PAIRING_TOKEN') or pairing.read_text().strip()
            async with connect(url, additional_headers={'Authorization': 'Bearer ' + token, 'X-CADPilot-Instance': identity},
                               user_agent_header='CADPilot/1.0',
                               max_size=16 * 1024 * 1024, open_timeout=30, ping_interval=20, ping_timeout=40) as socket:
                greeting = json.loads(await asyncio.wait_for(socket.recv(), 30))
                if greeting.get('credential'):
                    token = greeting['credential']
                    pending = credentials.with_suffix('.pending')
                    with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
                        json.dump({'origin': origin, 'instance': identity, 'token': token}, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                    pending.replace(credentials)
                await socket.send(json.dumps({'type': 'ack', 'credential': token}))
                app.state.connector_status = {'state': 'connected', 'portal': parsed.hostname}
                log.info('CAD computer connected to its account')
                delay = 1
                use_saved = True
                await serve_worker(app, socket)
                app.state.connector_status = {'state': 'disconnected', 'detail': 'Portal connection closed; reconnecting.'}
        except asyncio.CancelledError:
            raise
        except InvalidStatus as error:
            if error.response.status_code in (401, 403):
                use_saved = False  # A newly downloaded pairing file replaces a revoked credential.
            state, detail = connection_failure(error)
            app.state.connector_status = {'state': state, 'detail': detail}
            log.warning('%s Retrying.', detail)
        except Exception as error:
            # Never log URLs, headers, pairing codes or provider keys.
            state, detail = connection_failure(error)
            app.state.connector_status = {'state': state, 'detail': detail}
            log.warning('%s Retrying.', detail)
        await asyncio.sleep(delay + random.random())
        delay = min(30, delay * 2)


if __name__ == '__main__':
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:7800/healthz', timeout=5) as response:
            print(json.dumps(json.load(response), indent=2))
    except urllib.error.HTTPError as error:
        print(json.dumps(json.load(error), indent=2))
        raise SystemExit(1) from None
    except OSError:
        print('CAD service is not reachable on its internal port. Check the container logs.')
        raise SystemExit(1) from None
