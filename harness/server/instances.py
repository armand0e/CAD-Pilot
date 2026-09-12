"""Account-owned runtimes, expiring pairing codes and revocable worker credentials."""
import hashlib
import secrets
import sqlite3
import time
import uuid

from fastapi import HTTPException

PAIRING_SECONDS = 24 * 60 * 60  # slow first builds still get a full day to pair


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Instances:
    def __init__(self, auth_store):
        self.auth = auth_store
        with self.auth.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS instances (
                id TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                pairing_hash TEXT, pairing_expires REAL, worker_hash TEXT, completed INTEGER DEFAULT 0)''')

    def get(self, owner):
        with self.auth.db() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT id, name, completed FROM instances WHERE owner = ?', (owner,)).fetchone()
        return dict(row) if row else None

    def pair(self, owner):
        token = secrets.token_urlsafe(32)
        expires = time.time() + PAIRING_SECONDS
        with self.auth.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM instances WHERE owner = ?', (owner,)).fetchone()
            instance_id = row[0] if row else uuid.uuid4().hex
            db.execute('''INSERT INTO instances(id, owner, name, pairing_hash, pairing_expires) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner) DO UPDATE SET pairing_hash=excluded.pairing_hash,
                pairing_expires=excluded.pairing_expires, worker_hash=NULL, completed=0''',
                (instance_id, owner, 'My CAD computer', digest(token), expires))
        return {'id': instance_id, 'token': token, 'expires': expires}

    def authenticate_worker(self, instance_id, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        hashed = digest(token)
        with self.auth.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner, pairing_hash, pairing_expires, worker_hash FROM instances WHERE id = ?', (instance_id,)).fetchone()
            if not row:
                return None
            owner, pairing, expires, worker = row
            if worker and secrets.compare_digest(hashed, worker):
                return {'owner': owner, 'credential': None}
            if pairing and (expires or 0) > time.time() and secrets.compare_digest(hashed, pairing):
                credential = secrets.token_urlsafe(48)
                db.execute('UPDATE instances SET worker_hash=? WHERE id=?', (digest(credential), instance_id))
                return {'owner': owner, 'credential': credential}
        return None

    def acknowledge(self, instance_id, credential):
        with self.auth.db() as db:
            db.execute('UPDATE instances SET pairing_hash=NULL, pairing_expires=NULL WHERE id=? AND worker_hash=?',
                       (instance_id, digest(credential)))

    def complete(self, owner):
        with self.auth.db() as db:
            db.execute('UPDATE instances SET completed=1 WHERE owner=?', (owner,))

    def revoke(self, owner, instance_id):
        with self.auth.db() as db:
            changed = db.execute('UPDATE instances SET worker_hash=NULL, pairing_hash=NULL, completed=0 WHERE owner=? AND id=?',
                                 (owner, instance_id)).rowcount
        if not changed:
            raise HTTPException(404, 'No such instance')
