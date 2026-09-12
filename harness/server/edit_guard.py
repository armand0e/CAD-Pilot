"""Protect document content, not camera movements or who clicked a toolbar.

Only a complete, fresh inventory from the fixed FreeCAD observer can disprove
potential edits. Other applications retain conservative input-based protection.
Acknowledging a saved recipe never closes or writes an existing document.
"""
import asyncio
import time

from .native import read_native_state


def note_input(session, actor='user'):
    setattr(session, 'manual_changes' if actor == 'user' else 'agent_changes', True)
    session.content_check_after = time.time() + .6


def inventory(state):
    if not state or state.get('observer_error') or state.get('documents_truncated') is not False:
        return None
    identity = state.get('content_observer')
    docs = state.get('documents')
    if not isinstance(identity, str) or not identity or not isinstance(docs, list) or len(docs) > 64:
        return None
    result = {}
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get('token'), str) or not doc['token']:
            return None
        if doc['token'] in result or type(doc.get('epoch')) is not int or doc['epoch'] < 0:
            return None
        if type(doc.get('object_count')) is not int or doc['object_count'] < 0:
            return None
        if not isinstance(doc.get('filename'), str) or 'editing' not in doc:
            return None
        result[doc['token']] = {k: doc[k] for k in ('epoch', 'object_count', 'filename', 'editing')}
    return {'observer': identity, 'documents': result}


def reconcile(session, state):
    current = inventory(state)
    if current is None or state['timestamp'] < getattr(session, 'content_check_after', 0):
        return False
    baseline = getattr(session, 'content_baseline', None)
    if baseline and baseline['observer'] != current['observer']:
        return False  # An observer restart must not erase an edit history.
    previous = baseline['documents'] if baseline else {}
    changed = False
    for token, doc in current['documents'].items():
        if doc['editing']:
            changed = True
        elif token in previous:
            changed |= doc['epoch'] != previous[token]['epoch']
        else:
            # New empty untitled documents and focus/selection are safe setup.
            changed |= bool(doc['object_count'] or doc['filename'])
    if changed:
        return False
    session.content_baseline = current
    session.manual_changes = False
    session.agent_changes = False
    return True


async def native_edit_blocker(session):
    freecad = 'freecad' in getattr(session, 'app', {}).get('id', '').lower()
    if freecad:
        deadline = time.monotonic() + 2.5
        while True:
            state = read_native_state(getattr(session, 'state_dir', None))
            if reconcile(session, state):
                return None
            # Wait for a settling input/new observer, not for content to disappear.
            if inventory(state) is not None and state['timestamp'] >= getattr(session, 'content_check_after', 0):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(.15)
    elif not (getattr(session, 'manual_changes', False) or getattr(session, 'agent_changes', False)):
        return None
    actor = 'agent GUI work' if getattr(session, 'agent_changes', False) and not getattr(session, 'manual_changes', False) else 'viewport work'
    return (f'Native tools cannot safely incorporate the current {actor} into the saved recipe. '
            'Existing documents have been preserved. Stop the agent and choose “Use saved revision” '
            'in the Model panel to explicitly branch from the recipe, or continue in Visual-only mode. '
            'If no document was edited, the native observer may be unavailable; try a fresh session.')


async def acknowledge_saved(session, baseline=None):
    """Explicit user acknowledgment or an open-working-copy receipt, not a guess."""
    if baseline is None:
        deadline = time.monotonic() + 2.5
        while True:
            state = read_native_state(getattr(session, 'state_dir', None))
            if state and state['timestamp'] >= getattr(session, 'content_check_after', 0):
                baseline = inventory(state)
                if baseline is not None:
                    break
            if 'freecad' not in getattr(session, 'app', {}).get('id', '').lower() or time.monotonic() >= deadline:
                break
            await asyncio.sleep(.15)
    if baseline is None and 'freecad' in getattr(session, 'app', {}).get('id', '').lower():
        raise ValueError('FreeCAD has not supplied a complete document inventory. Saved-recipe acknowledgment was not applied; retry when the application is responsive.')
    session.content_baseline = baseline
    session.content_check_after = 0
    session.manual_changes = False
    session.agent_changes = False
