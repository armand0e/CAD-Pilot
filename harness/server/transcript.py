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
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def append(self, event):
        saved = dict(event)
        if saved['t'] == 'artifact':
            project = saved.pop('project', {})
            saved['saved_revision'] = {k: project.get(k) for k in ('id', 'head', 'name', 'geometry')}
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO events(id,payload) VALUES (?,?)',
                       (saved['event_id'], json.dumps(saved, ensure_ascii=False)))

    def read(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM events ORDER BY seq')]
