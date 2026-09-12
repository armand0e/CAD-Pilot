"""Private owner login with hashed passwords and revocable, expiring sessions."""
import asyncio
from contextlib import contextmanager
from http.cookies import SimpleCookie
import hashlib
import ipaddress
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.websockets import WebSocketDisconnect

COOKIE = 'cadpilot_session'
SESSION_SECONDS = 12 * 3600
ITERATIONS = 600_000


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex()


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS owner (name TEXT PRIMARY KEY, salt BLOB, digest TEXT);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, name TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS attempts (client TEXT PRIMARY KEY, started REAL, count INTEGER);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def has_owner(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM owner').fetchone() is not None

    def set_owner(self, name, password):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', name):
            raise ValueError('Owner name must be 1-64 letters, digits, or _.@-')
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError('Use a password of 12-1024 characters')
        salt = secrets.token_bytes(32)
        digest = password_hash(password, salt)
        with self.db() as db:
            db.execute('DELETE FROM owner')
            db.execute('INSERT INTO owner VALUES (?, ?, ?)', (name, salt, digest))
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM attempts')

    def register(self, name, password, client):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', name):
            raise HTTPException(422, 'Use 1-64 letters, digits, or _.@- for your username')
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise HTTPException(422, 'Use a password of at least 12 characters')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            key = 'signup:' + client
            db.execute('DELETE FROM attempts WHERE started < ?', (time.time() - 900,))
            row = db.execute('SELECT count FROM attempts WHERE client = ?', (key,)).fetchone()
            if row and row[0] >= 5:
                raise HTTPException(429, 'Too many account requests. Try again in 15 minutes.')
            db.execute('INSERT INTO attempts VALUES (?, ?, 1) ON CONFLICT(client) DO UPDATE SET count=count+1', (key, time.time()))
        salt = secrets.token_bytes(32)
        digest = password_hash(password, salt)
        try:
            with self.db() as db:
                db.execute('INSERT INTO owner VALUES (?, ?, ?)', (name, salt, digest))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'That username is already taken') from None

    def login(self, name, password, client):
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE started < ?', (now - 900,))
            db.execute('DELETE FROM sessions WHERE expires <= ?', (now,))
            attempt = db.execute('SELECT count FROM attempts WHERE client = ?', (client,)).fetchone()
            if attempt and attempt[0] >= 10:
                raise HTTPException(429, 'Too many login attempts. Try again in 15 minutes.', headers={'Retry-After': '900'})
            db.execute('INSERT INTO attempts VALUES (?, ?, 1) ON CONFLICT(client) DO UPDATE SET count=count+1', (client, now))
            row = db.execute('SELECT name, salt, digest FROM owner WHERE name = ?', (name,)).fetchone()
        # Always perform the same password work, even for an unknown username.
        digest = password_hash(password, row[1] if row else b'\0' * 32)
        if not row or not hmac.compare_digest(digest, row[2]) or not hmac.compare_digest(name.encode(), row[0].encode()):
            raise HTTPException(401, 'Incorrect username or password')
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A concurrent password reset must not authorize the old password.
            current = db.execute('SELECT digest FROM owner WHERE name = ?', (name,)).fetchone()
            if not current or not hmac.compare_digest(current[0], digest):
                raise HTTPException(401, 'Credentials changed. Sign in again.')
            db.execute('DELETE FROM attempts WHERE client = ?', (client,))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (self.token_hash(token), name, now + SESSION_SECONDS))
        return token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def user(self, token):
        if not token or len(token) > 200:
            return None
        with self.db() as db:
            row = db.execute('SELECT name FROM sessions WHERE digest = ? AND expires > ?', (self.token_hash(token), time.time())).fetchone()
        return row[0] if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE digest = ?', (self.token_hash(token),))


def scope_token(scope, cookie=COOKIE):
    try:
        cookies = SimpleCookie()
        cookies.load(dict(scope['headers']).get(b'cookie', b'').decode('latin-1'))
        return cookies[cookie].value if cookie in cookies else ''
    except Exception:
        return ''


class OwnerAuthentication:
    def __init__(self, app, store, cookie=COOKIE, public_paths=()):
        self.app, self.store = app, store
        self.cookie, self.public_paths = cookie, set(public_paths)

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        public = {'/healthz', '/login', '/api/auth/login', '/api/auth/session',
                  '/static/login.css', '/static/login.js'}
        if (scope['type'] == 'http' and scope['path'] in public) or scope['path'] in self.public_paths:
            return await self.app(scope, receive, send)
        token = scope_token(scope, self.cookie)
        user = await asyncio.to_thread(self.store.user, token)
        if not user:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 4401})
            response = RedirectResponse('/login', status_code=303) if scope['path'] == '/' else JSONResponse({'detail': 'Sign in to continue'}, status_code=401)
            return await response(scope, receive, send)
        scope['cadpilot_user'] = user
        if scope['type'] == 'http':
            return await self.app(scope, receive, send)

        async def authenticated_receive():
            message = await receive()
            if message['type'] != 'websocket.disconnect' and not await asyncio.to_thread(self.store.user, token):
                raise WebSocketDisconnect(4401)
            return message

        async def revoked():
            while True:
                await asyncio.sleep(30)
                if not await asyncio.to_thread(self.store.user, token):
                    return

        application = asyncio.create_task(self.app(scope, authenticated_receive, send))
        watcher = asyncio.create_task(revoked())
        try:
            done, _ = await asyncio.wait((application, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await send({'type': 'websocket.close', 'code': 4401})
            else:
                await application
        finally:
            for task in (application, watcher):
                task.cancel()
            await asyncio.gather(application, watcher, return_exceptions=True)


def install(app, root, *, registration=False, public_origin='', public_paths=()):
    enabled = registration or os.environ.get('CADPILOT_AUTH_ENABLED', '0') == '1'
    cookie = 'cadpilot_portal_session' if registration else COOKIE
    store = AuthStore(root / 'state/auth.sqlite3') if enabled else None
    if store is not None:
        if not registration and not store.has_owner():
            raise RuntimeError('No owner configured. Run python -m server.auth bootstrap first.')
        app.add_middleware(OwnerAuthentication, store=store, cookie=cookie,
                           public_paths=(*public_paths, *(['/api/auth/register'] if registration else [])))

    @app.get('/healthz')
    async def healthz():
        connector = getattr(app.state, 'connector_status', None)
        if connector is None:
            return {'ok': True}
        ready = connector['state'] == 'connected'
        return JSONResponse({'ok': ready, 'connector': connector}, status_code=200 if ready else 503)

    @app.get('/login')
    async def login_page():
        return FileResponse(root / 'web/login.html') if enabled else RedirectResponse('/')

    @app.get('/api/auth/session')
    async def session(request: Request):
        user = await asyncio.to_thread(store.user, scope_token(request.scope, cookie)) if store else None
        return {'enabled': enabled, 'authenticated': bool(user) or not enabled, 'username': user,
                'registration': registration, 'deployment': 'portal' if registration else 'standalone'}

    def client_identity(request):
        peer = request.client.host if request.client else 'local'
        # Opt-in only for the loopback-published Cloudflare Tunnel portal.
        if registration and os.environ.get('CADPILOT_TRUST_CLOUDFLARE') == '1':
            try:
                return str(ipaddress.ip_address(request.headers.get('cf-connecting-ip', '')))
            except ValueError:
                pass
        return peer

    @app.post('/api/auth/login')
    async def login(request: Request, body: dict):
        if not store:
            raise HTTPException(404)
        name, password = body.get('username'), body.get('password')
        if not isinstance(name, str) or not isinstance(password, str) or len(name) > 64 or not 1 <= len(password) <= 1024:
            raise HTTPException(401, 'Incorrect username or password')
        token = await asyncio.to_thread(store.login, name, password, client_identity(request))
        response = JSONResponse({'authenticated': True, 'username': name})
        response.set_cookie(cookie, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=public_origin.startswith('https://') or request.url.scheme == 'https', samesite='strict', path='/')
        return response

    if registration:
        @app.post('/api/auth/register')
        async def register(request: Request, body: dict):
            await asyncio.to_thread(store.register, body.get('username'), body.get('password'), client_identity(request))
            return await login(request, body)

    @app.post('/api/auth/logout')
    async def logout(request: Request):
        if store:
            await asyncio.to_thread(store.logout, scope_token(request.scope, cookie))
        response = JSONResponse({'authenticated': False})
        response.delete_cookie(cookie, path='/', httponly=True, samesite='strict')
        return response

    return store


def main():
    import getpass
    import sys
    root = Path(__file__).resolve().parents[1]
    store = AuthStore(root / 'state/auth.sqlite3')
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'bootstrap':
        if store.has_owner():
            return
        password_file = os.environ.get('CADPILOT_PASSWORD_FILE')
        if not password_file:
            raise SystemExit('CADPILOT_PASSWORD_FILE must name the initial owner password file')
        password = Path(password_file).read_text().rstrip('\r\n')
        store.set_owner(os.environ.get('CADPILOT_OWNER', 'admin'), password)
    elif command == 'reset':
        password = getpass.getpass('New password (12+ characters): ')
        if password != getpass.getpass('Confirm password: '):
            raise SystemExit('Passwords differ; nothing changed')
        store.set_owner(os.environ.get('CADPILOT_OWNER', 'admin'), password)
        print('Password changed; all login sessions revoked.')
    else:
        raise SystemExit('Usage: python -m server.auth {bootstrap|reset}')


if __name__ == '__main__':
    main()
