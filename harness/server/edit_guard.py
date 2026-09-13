"""Protect document content, not camera movements or who clicked a toolbar.

Only a complete, fresh inventory from the fixed FreeCAD observer can disprove
potential edits. Other applications retain conservative input-based protection.
Acknowledging a saved recipe never closes or writes an existing document.
"""
import asyncio
import math
import time

from .native import read_native_state


class NativeEditBlocked(ValueError):
    """An observation/content conflict, not invalid tool arguments or geometry."""


def note_input(session, actor='user'):
    setattr(session, 'manual_changes' if actor == 'user' else 'agent_changes', True)
    session.content_check_after = time.time() + .6


def inventory(state):
    if not state or state.get('observer_error') or state.get('documents_truncated') is not False:
        return None
    timestamp = state.get('timestamp')
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp <= 0:
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
    return {'observer': identity, 'documents': result, 'timestamp': timestamp}


def observation_floor(session):
    baseline = getattr(session, 'content_baseline', None) or {}
    return max(getattr(session, 'content_check_after', 0), baseline.get('timestamp', 0))


def observation_problem(session, current):
    if current is None:
        return 'FreeCAD has not supplied a complete, fresh document observation.'
    if current['timestamp'] < observation_floor(session):
        return 'FreeCAD is still reporting an observation from before the latest acknowledged revision or input.'
    baseline = getattr(session, 'content_baseline', None)
    if baseline and baseline['observer'] != current['observer']:
        return 'The FreeCAD observer restarted, so its document edit history cannot be compared.'
    previous = baseline['documents'] if baseline else {}
    for token, doc in current['documents'].items():
        if doc['editing']:
            return 'A FreeCAD document is currently in edit mode.'
        if token in previous:
            if doc['epoch'] != previous[token]['epoch']:
                return 'A FreeCAD document changed after the saved revision was acknowledged.'
        elif doc['object_count'] or doc['filename']:
            return 'A populated FreeCAD document appeared outside the acknowledged saved revision.'
    return None


def reconcile(session, state):
    current = inventory(state)
    if observation_problem(session, current):
        return False
    session.content_baseline = current
    session.manual_changes = False
    session.agent_changes = False
    return True


async def native_edit_blocker(session):
    freecad = 'freecad' in getattr(session, 'app', {}).get('id', '').lower()
    reason = ''
    if freecad:
        deadline = time.monotonic() + 2.5
        while True:
            state = read_native_state(getattr(session, 'state_dir', None))
            if reconcile(session, state):
                return None
            reason = observation_problem(session, inventory(state))
            # Wait for a settling input/new observer, not for content to disappear.
            if inventory(state) is not None and state['timestamp'] >= observation_floor(session):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(.15)
    elif not (getattr(session, 'manual_changes', False) or getattr(session, 'agent_changes', False)):
        return None
    actor = 'agent GUI work' if getattr(session, 'agent_changes', False) and not getattr(session, 'manual_changes', False) else 'viewport work'
    return (f'Native tools cannot safely incorporate the current {actor} into the saved recipe. {reason} '
            'Existing documents have been preserved. Stop the agent and choose “Use saved revision” '
            'in the Model panel to explicitly branch from the recipe, or continue in Visual-only mode. '
            'Read-only inspection and chat remain available; do not retry writes until this is resolved.')


async def acknowledge_saved(session, baseline=None):
    """Explicit user acknowledgment or an open-working-copy receipt, not a guess."""
    if baseline is None:
        deadline = time.monotonic() + 2.5
        while True:
            state = read_native_state(getattr(session, 'state_dir', None))
            if state and state['timestamp'] >= observation_floor(session):
                baseline = inventory(state)
                if baseline is not None:
                    break
            if 'freecad' not in getattr(session, 'app', {}).get('id', '').lower() or time.monotonic() >= deadline:
                break
            await asyncio.sleep(.15)
    if baseline is None and 'freecad' in getattr(session, 'app', {}).get('id', '').lower():
        raise ValueError('FreeCAD has not supplied a complete document inventory. Saved-recipe acknowledgment was not applied; retry when the application is responsive.')
    session.content_baseline = baseline
    session.content_check_after = baseline['timestamp'] if baseline else 0
    session.manual_changes = False
    session.agent_changes = False
