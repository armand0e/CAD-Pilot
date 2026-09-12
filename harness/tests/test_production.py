"""Local workspace protocol, planning, observation and input recovery regressions."""
import asyncio
import json
import sys
import tempfile
import threading
import subprocess
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner
from server.app import app, _desktop_input
from server.observation import RecoveryBudget, has_application_content, screen_change
from server.planning import parse_plan
from server.protocol import desktop_message, decode_message, same_origin
from server.sessions import SessionManager
from server.journal import EventJournal
from server.xscreen import XScreen
from server.processes import ProcessLog, stop_process
from server.native import read_native_state
import test_agent as fixtures


def plan(objective="Select the horizontal edge", **extra):
    return json.dumps({"decision": "act", "objective": objective,
                       "expected_result": "The edge is highlighted", "message": "I’ll continue dimensioning the plate.",
                       "wait_seconds": 0, **extra})


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, steps=3):
        return fixtures.AgentTests().runner(max_steps=steps)

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.005)

    async def test_continue_goes_through_planner_and_preserves_original_goal(self):
        runner = self.runner()
        runner._plan_intent = AgentRunner._plan_intent.__get__(runner)
        calls = []
        async def chat(endpoint, messages, **kwargs):
            calls.append(messages)
            return plan()
        runner._chat = chat
        runner.start("Build a 60 x 40 x 8 plate", "manual")
        await self.until(lambda: runner.phase == "awaiting")
        await runner.wait_stopped()
        runner.start("continue please", "manual")
        await self.until(lambda: runner.phase == "awaiting")
        self.assertEqual(runner.task_text, "Build a 60 x 40 x 8 plate")
        self.assertEqual(len(runner.history_lines), 2)
        self.assertIn("continue please", json.dumps(calls[-1]))
        self.assertIn("60 x 40 x 8", json.dumps(calls[-1]))
        self.assertFalse(any(e["t"] == "intent" and e["text"] == "continue please" for e in runner.events))
        await runner.wait_stopped()

    async def test_steering_cancels_stale_model_request_before_any_input(self):
        runner = self.runner(1)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        calls = 0
        async def policy(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return [{"type":"key", "key":"n", "modifiers":[]}], "", None
        runner._policy_actions = policy
        runner.start("Build", "auto")
        await asyncio.wait_for(entered.wait(), 1)
        runner.submit_intent("Make the plate thicker instead")
        await asyncio.wait_for(runner._task, 2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(runner.session.screen.actions, ["n"])
        self.assertIn("Make the plate thicker instead", runner.user_messages)

    async def test_steering_during_cursor_delay_does_not_inject_old_click(self):
        runner = self.runner(1)
        runner.config["agent"]["action_delay_s"] = .2
        runner.start("Build", "auto")
        await self.until(lambda: any(e["t"] == "action" for e in runner.events))
        runner.submit_intent("Use a different operation")
        await asyncio.wait_for(runner._task, 2)
        self.assertEqual(runner.session.screen.actions, ["2"])

    async def test_two_uncertain_reviews_do_not_pause_useful_work(self):
        runner = self.runner(3)
        async def review(*args, **kwargs):
            return {"status":"uncertain", "observation":"Selection highlight is subtle", "next_objective":"Inspect selection"}
        runner._assess_outcome = review
        runner.start("Build", "auto")
        await asyncio.wait_for(runner._task, 2)
        self.assertEqual(runner.completed_steps, 3)
        self.assertFalse(any(e["t"] == "pause" and e["paused"] for e in runner.events))

    async def test_wait_plan_does_not_fabricate_a_desktop_click(self):
        runner = self.runner(1)
        async def planner(*args):
            runner.plan_details = json.loads(plan(decision="wait", objective="", wait_seconds=.5))
            return ""
        runner._plan_intent = planner
        runner.start("Build", "auto")
        await self.until(lambda: runner.phase == "waiting_screen")
        await runner.wait_stopped()
        self.assertEqual(runner.session.screen.actions, [])
        self.assertEqual(runner.completed_steps, 0)

    async def test_reviewer_offline_has_specific_pause_not_false_no_progress(self):
        runner = self.runner()
        async def review(*args, **kwargs):
            return {"status":"uncertain", "available":False, "observation":"Reviewer endpoint unavailable", "next_objective":""}
        runner._assess_outcome = review
        runner.start("Build", "auto")
        await self.until(lambda: runner.paused)
        self.assertIn("reviewer", runner.snapshot()["pause_reason"])
        self.assertEqual(runner.completed_steps, 1)
        await runner.wait_stopped()

    async def test_new_guidance_is_explicitly_unhandled_not_eclipsed_by_old_success(self):
        runner = self.runner()
        runner.start("Open a document", "manual")
        await self.until(lambda: runner.phase == "awaiting")
        self.assertIsNone(runner.pending_guidance)
        runner.submit_intent("Press Ctrl+N once again")
        self.assertIn('NOT YET ACTED ON: "Press Ctrl+N once again"', runner._supervision_context())
        await runner.wait_stopped()

    async def test_status_question_does_not_execute_a_modeling_action(self):
        runner = self.runner()
        async def planner(*args):
            runner.plan_details = json.loads(plan(decision="respond", objective="", message="The sketch is not dimensioned yet."))
            return ""
        runner._plan_intent = planner
        runner.start("What is left to do?", "auto")
        await runner._task
        self.assertEqual(runner.session.screen.actions, [])
        self.assertTrue(any(e["t"] == "assistant" and "dimensioned" in e["message"] for e in runner.events))

    async def test_question_during_guided_work_answers_then_keeps_session_waiting(self):
        runner = self.runner()
        runner.start("Build a plate", "manual")
        await self.until(lambda: runner.phase == "awaiting")
        async def planner(*args):
            runner.plan_details = json.loads(plan(decision="respond",objective="",message="The width still needs constraining."))
            return ""
        runner._plan_intent = planner
        runner.submit_intent("What is left to do?")
        await self.until(lambda: runner._last_reply is not None)
        await self.until(lambda: runner.phase == "awaiting")
        self.assertTrue(runner.active)
        self.assertEqual(runner.completed_steps,1)
        self.assertIsNone(runner.pending_guidance)
        await runner.wait_stopped()

    async def test_steering_during_atomic_input_preserves_pending_guidance(self):
        runner = self.runner(2)
        entered, release = threading.Event(), threading.Event()
        original = runner._execute
        def execute(action):
            entered.set()
            release.wait(2)
            original(action)
        runner._execute = execute
        contexts = []
        original_plan = runner._plan_intent
        async def planner(*args):
            contexts.append(runner._supervision_context())
            return await original_plan(*args)
        runner._plan_intent = planner
        runner.start("Build", "auto")
        await self.until(entered.is_set)
        runner.submit_intent("Make the holes 6 mm instead")
        release.set()
        await runner._task
        self.assertIn('NOT YET ACTED ON: "Make the holes 6 mm instead"', contexts[-1])


class ProtocolTests(unittest.TestCase):
    def test_bad_messages_cannot_reach_input_injection(self):
        for value in ("[]", '"click"', '{"x":1}', "x" * 32769):
            with self.assertRaises(ValueError):
                decode_message(value)
        for value in (
            {"t":"move", "x":True, "y":3}, {"t":"move", "x":100, "y":3},
            {"t":"key", "key":"a", "modifiers":["bad"]},
            {"t":"scroll", "x":1,"y":2,"delta_y":5000},
            {"t":"text", "text":"a" * 8193}):
            with self.assertRaises(ValueError):
                desktop_message(value, 100, 100)

    def test_horizontal_scroll_and_modifiers_survive_protocol(self):
        value = desktop_message({"t":"scroll", "x":1,"y":2,"delta_x":-1,"modifiers":["ctrl"]}, 100,100)
        self.assertEqual((value["delta_x"],value["delta_y"],value["modifiers"]), (-1,0,["ctrl"]))

    def test_same_origin_rejects_deceptive_hosts_and_null(self):
        for origin in ("null", "https://evil.test", "http://localhost:7800.evil.test", "http://localhost:7800@evil.test"):
            self.assertFalse(same_origin(origin, "localhost:7800"))
        self.assertTrue(same_origin("http://localhost:7800", "localhost:7800"))

    def test_foreign_site_cannot_control_desktop_over_http_or_websocket(self):
        client = TestClient(app, base_url="http://127.0.0.1")
        response = client.post("/api/sessions", json={"app_id":"unused"}, headers={"origin":"https://evil.test"})
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(WebSocketDisconnect) as context:
            with client.websocket_connect("ws://127.0.0.1/ws/agent/unused", headers={"origin":"https://evil.test"}):
                pass
        self.assertEqual(context.exception.code, 4403)
        self.assertEqual(client.get("/", headers={"host":"evil.test"}).status_code, 400)
        self.assertIn("frame-ancestors 'none'", client.get("/").headers["content-security-policy"])

    def test_invalid_session_selector_is_a_client_error(self):
        response = TestClient(app, base_url="http://127.0.0.1").post("/api/sessions",json={"app_id":[]})
        self.assertEqual(response.status_code,422)

    def test_chunked_request_cannot_bypass_size_limit(self):
        client = TestClient(app, base_url="http://127.0.0.1")
        response = client.post("/api/sessions",content=iter([b"a" * 20000, b"b" * 20000]))
        self.assertEqual(response.status_code,413)

    def test_app_log_is_bounded_and_does_not_block_child(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.log"
            proc = subprocess.Popen([sys.executable,"-c","import sys; sys.stdout.write('x' * 100000)"],
                                    stdout=subprocess.PIPE,start_new_session=True)
            log = ProcessLog(proc.stdout,path,limit=1024)
            try:
                self.assertEqual(proc.wait(timeout=3),0)
                log.thread.join(timeout=2)
                self.assertEqual(path.stat().st_size,1024)
            finally:
                stop_process(proc)
                log.close()

    def test_plan_requires_micro_outcome_and_bounded_wait(self):
        self.assertEqual(parse_plan(plan())["expected_result"], "The edge is highlighted")
        for value in (plan(expected_result=""), plan(wait_seconds=10), plan(decision="ask",message="")):
            with self.assertRaises(ValueError):
                parse_plan(value)

    def test_bright_cursor_is_not_a_ready_cad_application(self):
        image = Image.new("RGB",(100,100))
        image.putpixel((50,50),(255,255,255))
        self.assertFalse(has_application_content(image))
        self.assertTrue(has_application_content(Image.new("RGB",(100,100),"white")))
        self.assertEqual(screen_change(image,image), 0)

    def test_recovery_has_distinct_ambiguity_and_failure_budgets(self):
        budget = RecoveryBudget()
        self.assertFalse(budget.observe("uncertain"))
        self.assertFalse(budget.observe("uncertain"))
        self.assertEqual(budget.failures, 0)
        budget.observe("progress")
        self.assertEqual(budget.uncertain,0)
        for _ in range(3):
            self.assertFalse(budget.observe("blocked"))
        self.assertTrue(budget.observe("blocked"))

    def test_session_limit_is_checked_before_starting_any_process(self):
        manager = SessionManager(max_sessions=1)
        manager.sessions["existing"] = object()
        with patch.object(manager, "_start_xserver") as start:
            with self.assertRaisesRegex(RuntimeError, "Session limit"):
                manager.create({"name":"FreeCAD"})
            start.assert_not_called()

    def test_journal_retains_events_and_bounds_disk_use(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory))
            journal.append({"t":"user", "text":"Build a plate"})
            self.assertIn("Build a plate", journal.path.read_text())
            journal.bytes_written = 16 * 1024 * 1024
            journal.append({"t":"note"})
            self.assertIn("limit", journal.failed)

    def test_native_state_requires_fresh_bounded_real_file(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "native-state.json"
            value = {"schema":1,"application":"FreeCAD","timestamp":time.time(),"selection":[]}
            path.write_text(json.dumps(value))
            self.assertEqual(read_native_state(directory)["selection"], [])
            value["timestamp"] -= 6
            path.write_text(json.dumps(value))
            self.assertIsNone(read_native_state(directory))
            path.write_text("x" * 65537)
            self.assertIsNone(read_native_state(directory))
            path.unlink()
            path.symlink_to(directory / "other")
            self.assertIsNone(read_native_state(directory))


class InputTests(unittest.IsolatedAsyncioTestCase):
    async def test_modifier_mouse_and_horizontal_wheel_dispatch(self):
        calls = []
        screen = SimpleNamespace(width=100,height=100,
            pointer_down=lambda **kw:calls.append(kw), scroll=lambda **kw:calls.append(kw))
        session = SimpleNamespace(screen=screen)
        await _desktop_input(session,{"t":"down","x":1,"y":2,"modifiers":["ctrl"]})
        await _desktop_input(session,{"t":"scroll","x":1,"y":2,"delta_x":1})
        self.assertEqual(calls[0]["modifiers"],["ctrl"])
        self.assertEqual(calls[1]["delta_x"],1)

    def test_typing_preflights_entire_string_before_sending_any_character(self):
        screen = object.__new__(XScreen)
        screen.lock = threading.RLock()
        screen.display = SimpleNamespace(keysym_to_keycode=lambda s: 0 if s>127 else 42,
                                         keycode_to_keysym=lambda *args:ord("a"))
        with patch("server.xscreen.xtest.fake_input") as send:
            with self.assertRaisesRegex(ValueError,"not mapped"):
                screen.type_text("abc\u2603")
            send.assert_not_called()
