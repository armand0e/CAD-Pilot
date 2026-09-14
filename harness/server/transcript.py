"""Durable UI history, separate from the bounded, curated model context.

Each event has a globally stable identity. SQLite commits before broadcast, so a
reconnect/reopened project can replay even an interrupted answer. No screenshots,
model prompts or hidden reasoning are stored here. Displayable provider reasoning
and bounded draft tool input are retained so their timelines can be reconstructed.
"""
import json
import sqlite3


class Transcript:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
            if 'kind' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute('ALTER TABLE events ADD COLUMN kind TEXT')
                db.execute("UPDATE events SET kind=json_extract(payload, '$.t')")
            db.execute("CREATE INDEX IF NOT EXISTS input_events ON events(seq) WHERE kind IN ('user','answer')")

    def connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def append(self, event):
        saved = dict(event)
        if saved['t'] == 'artifact':
            project = saved.pop('project', {})
            saved['saved_revision'] = {k: project.get(k) for k in ('id', 'head', 'name', 'geometry')}
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO events(id,payload,kind) VALUES (?,?,?)',
                       (saved['event_id'], json.dumps(saved, ensure_ascii=False), saved['t']))

    def input_events(self, after=0):
        """Read only new user messages/question answers, using the input index."""
        with self.connect() as db:
            rows = list(db.execute("SELECT seq,payload FROM events WHERE kind IN ('user','answer') AND seq>? ORDER BY seq", (after,)))
        return (rows[-1][0] if rows else after), [json.loads(row[1]) for row in rows]

    def read(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM events ORDER BY seq')]
