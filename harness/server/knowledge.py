"""Consultable design knowledge: short rule documents and a curated footprint library.

Both are data files under harness/knowledge. They are read fresh on each call so the
user can edit them without restarting.
"""
import json
import re
import time
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parents[1] / 'knowledge'
MAX_NOTES = 6000


def _tokens(text):
    return set(re.findall(r'[a-z0-9]+', (text or '').lower()))


def design_notes(topic):
    """The one or two note documents that best match the topic (plus their names)."""
    documents = []
    for path in sorted(KNOWLEDGE.glob('*.md')):
        # Older images left this software guide in the knowledge volume. The
        # current version is supplied as /work/CAD_GUIDE.md; don't serve stale copies.
        if path.name == 'source-workspace.md':
            continue
        text = path.read_text(errors='replace')
        title = text.splitlines()[0].lstrip('# ').strip() if text else path.stem
        score = len(_tokens(topic) & (_tokens(path.stem) | _tokens(title))) * 3 + len(_tokens(topic) & _tokens(text))
        documents.append((score, path.stem, title, text))
    documents.sort(key=lambda d: -d[0])
    chosen = [d for d in documents[:2] if d[0] > 0] or documents[:1]
    notes, total = [], 0
    for _, stem, title, text in chosen:
        body = text[:MAX_NOTES - total]
        total += len(body)
        notes.append({'document': stem, 'title': title, 'text': body})
        if total >= MAX_NOTES:
            break
    return {'topic': topic, 'available': [d[1] for d in documents], 'notes': notes,
            'notice': 'General design rules, not measurements of a specific product; product dimensions still need a source.'}


LEARNED = KNOWLEDGE / 'learned_facts.jsonl'


def remember_facts(facts, source, project=None):
    """Append facts the agent extracted from a page it read (with URL and verbatim quote).

    This is the agent's own cross-project memory of sourced measurements; nothing is
    hand-written into it. Duplicate statements from the same URL are skipped.
    """
    if not facts or not source:
        return 0
    existing = {(r.get('statement'), r.get('source_url')) for r in recall_all()}
    added = 0
    with LEARNED.open('a') as handle:
        for fact in facts:
            key = (fact.get('statement'), source.get('url'))
            if key in existing or not fact.get('statement'):
                continue
            handle.write(json.dumps({'statement': fact['statement'][:600], 'quote': fact.get('quote', '')[:600], 'source_url': source.get('url'),
                                     'source_title': (source.get('title') or '')[:200], 'project': project, 'added_at': time.time()}) + '\n')
            existing.add(key)
            added += 1
    return added


def recall_all():
    if not LEARNED.exists():
        return []
    records = []
    for line in LEARNED.read_text(errors='replace').splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records[-5000:]


def recall_facts(query):
    """Previously extracted, sourced facts whose text shares words with the query."""
    wanted = _tokens(query)
    scored = []
    for record in recall_all():
        overlap = len(wanted & _tokens(record.get('statement', '') + ' ' + record.get('source_title', '')))
        if overlap >= 2 or (overlap == 1 and len(wanted) == 1):
            scored.append((overlap, record))
    scored.sort(key=lambda r: (-r[0], -r[1].get('added_at', 0)))
    facts = [{k: v for k, v in r.items() if k in ('statement', 'quote', 'source_url', 'source_title', 'added_at')} for _, r in scored[:12]]
    return {'query': query, 'facts': facts, 'stored_total': len(recall_all()),
            'notice': ('These are facts this agent extracted from pages it read in earlier work, with their sources; re-read the source if the variant matters.'
                       if facts else 'Nothing remembered for this query; research it (search, read, images) and the extracted facts will be remembered.')}


def fact_statements(facts):
    return [f"{f['statement']} (source: {f.get('source_url')})" for f in facts]
