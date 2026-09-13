import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server.edit_guard import inventory, reconcile, native_edit_blocker, acknowledge_saved, note_input


def state(*docs):
    return {'timestamp': time.time(), 'content_observer': 'test-observer',
            'documents_truncated': False, 'documents': list(docs)}


def document(token='doc', epoch=1, objects=3, filename=''):
    return {'token': token, 'epoch': epoch, 'object_count': objects, 'filename': filename, 'editing': None}


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        return SimpleNamespace(app={'id': 'freecad'}, manual_changes=False, agent_changes=False)

    def test_empty_session_click_and_agent_setup_do_not_poison_native_tools(self):
        s = self.session()
        note_input(s)
        note_input(s, 'agent')
        snap = state()
        snap['timestamp'] = s.content_check_after + .01
        self.assertTrue(reconcile(s, snap))
        self.assertFalse(s.manual_changes or s.agent_changes)

    def test_camera_selection_and_new_empty_document_do_not_count_as_edits(self):
        s = self.session()
        snap = state(document())
        s.content_baseline = inventory(snap)
        snap.update(selection=['Stock'], focus={'name': 'view'}, camera='changed')
        snap['documents'].append(document('empty', objects=0))
        s.manual_changes = True
        self.assertTrue(reconcile(s, snap))

    def test_background_edit_not_hidden_by_empty_active_document(self):
        s = self.session()
        s.content_baseline = inventory(state(document()))
        snap = state(document(epoch=2), document('empty', objects=0))
        snap['document'] = None
        self.assertFalse(reconcile(s, snap))

    def test_new_imported_or_unsaved_geometry_is_never_silently_adopted(self):
        for doc in (document(), document(objects=0, filename='/manual.FCStd')):
            self.assertFalse(reconcile(self.session(), state(doc)))

    def test_late_state_restart_truncation_and_unknown_inventory_fail_closed(self):
        s = self.session()
        s.content_baseline = inventory(state(document()))
        for change in ({'content_observer': 'restarted'}, {'documents_truncated': True},
                       {'observer_error': 'incomplete'}, {'documents': None}, {'documents': [{}]}):
            snap = state(document()); snap.update(change)
            self.assertFalse(reconcile(s, snap))
        note_input(s)
        self.assertFalse(reconcile(s, state(document())))

    def test_editing_sketch_and_deleting_all_known_geometry_remain_protected(self):
        s = self.session()
        s.content_baseline = inventory(state(document()))
        self.assertFalse(reconcile(s, state(dict(document(), editing='Sketch'))))
        self.assertFalse(reconcile(s, state(document(epoch=2, objects=0))))

    async def test_explicit_branch_acknowledges_current_inventory_then_detects_next_edit(self):
        s = self.session(); s.manual_changes = True
        snap = state(document(epoch=4))
        with patch('server.edit_guard.read_native_state', return_value=snap):
            await acknowledge_saved(s)
            self.assertIsNone(await native_edit_blocker(s))
        self.assertFalse(reconcile(s, state(document(epoch=5))))

    async def test_open_receipt_cannot_be_rolled_back_by_the_previous_observer_frame(self):
        s = self.session()
        old = state(document('previous'))
        receipt = state(document('previous'), document('opened'))
        await acknowledge_saved(s, inventory(receipt))
        baseline = copy.deepcopy(s.content_baseline)
        self.assertFalse(reconcile(s, old))  # Includes status polling during presentation.
        self.assertEqual(s.content_baseline, baseline)
        fresh = state(document('previous'), document('opened'))
        with patch('server.edit_guard.read_native_state', side_effect=[old, fresh]):
            self.assertIsNone(await native_edit_blocker(s))
        self.assertFalse(reconcile(s, state(document('previous', epoch=2), document('opened'))))

    def test_old_observations_cannot_restore_a_closed_document_or_erase_newer_edits(self):
        s = self.session()
        original = state(document())
        s.content_baseline = inventory(original)
        self.assertTrue(reconcile(s, state()))  # Closing an unchanged document is allowed.
        self.assertFalse(reconcile(s, original))
        self.assertEqual(s.content_baseline['documents'], {})
        self.assertFalse(reconcile(s, state(document(epoch=2))))

    def test_inventory_requires_a_finite_timestamp_including_open_receipts(self):
        for timestamp in (None, 0, True, float('nan'), float('inf')):
            self.assertIsNone(inventory(dict(state(document()), timestamp=timestamp)))

    async def test_agent_edits_are_not_blame_assigned_to_user(self):
        s = self.session(); s.agent_changes = True
        with patch('server.edit_guard.read_native_state', return_value=state(document())):
            self.assertIn('agent GUI work', await native_edit_blocker(s))

    async def test_other_app_unknown_inputs_stay_conservative(self):
        s = self.session(); s.app['id'] = 'openscad'
        self.assertIsNone(await native_edit_blocker(s))
        note_input(s)
        self.assertIsNotNone(await native_edit_blocker(s))
