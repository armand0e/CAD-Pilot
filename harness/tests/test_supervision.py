import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.supervision import RepetitionGuard, equivalent_action, parse_review


class SupervisionTests(unittest.TestCase):
    def click(self, x=146, y=72, **kwargs):
        return {"type": "click", "x": x, "y": y, "button": "left", **kwargs}

    def test_small_jitter_and_optional_defaults_cannot_bypass_guard(self):
        guard = RepetitionGuard()
        guard.record(self.click())
        guard.record(self.click(148, 74, modifiers=[], clicks=1))
        self.assertIsNotNone(guard.check([self.click(150, 76)]))

    def test_buttons_modifiers_text_and_distinct_targets_are_not_equivalent(self):
        self.assertFalse(equivalent_action(self.click(), self.click(modifiers=["ctrl"])))
        self.assertFalse(equivalent_action(self.click(), self.click(200)))
        self.assertFalse(equivalent_action({"type": "type_text", "text": "a"}, {"type": "type_text", "text": "b"}))

    def test_cyclic_actions_block_even_when_no_consecutive_duplicates(self):
        guard = RepetitionGuard()
        a, b = self.click(), self.click(400)
        for action in [a, b, a, b]:
            guard.record(action)
        self.assertIn("cycle", guard.check([a, b]))

    def test_repeated_actions_inside_one_batch_are_rejected_atomically(self):
        guard = RepetitionGuard()
        self.assertIsNotNone(guard.check([self.click()] * 3))
        self.assertEqual(len(guard.executed), 0)
        self.assertIsNone(guard.check([self.click()]))

    def test_escape_can_exit_a_tool_but_does_not_disable_click_cycle_guard(self):
        guard = RepetitionGuard()
        esc = {"type":"key", "key":"Escape", "modifiers":[]}
        for action in [esc,self.click(),esc,self.click()]:
            guard.record(action)
        self.assertIsNone(guard.check([esc]))
        guard.record(esc)
        self.assertIsNotNone(guard.check([self.click()]))

    def test_review_requires_evidence_and_known_status(self):
        for raw in ('[]', '{"status":"success","observation":"ok","next_objective":""}',
                    '{"status":"achieved","observation":"","next_objective":""}'):
            with self.assertRaises(ValueError):
                parse_review(raw)
