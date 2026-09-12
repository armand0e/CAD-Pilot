"""Agent lifecycle regressions. Run with harness/.venv/bin/python -m unittest discover -s harness/tests."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.agent import AgentRunner, repair_completion, TruncatedCompletion


class Screen:
    width = 100
    height = 100

    def __init__(self):
        self.actions = []

    def capture_image(self):
        return Image.new("RGB", (100, 100), "#444444")

    def key(self, key, modifiers):
        self.actions.append(key)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, max_steps=3):
        screen = Screen()
        runner = AgentRunner(SimpleNamespace(screen=screen, app={"name": "CAD"}),
                             {"agent": {"max_steps": max_steps, "action_delay_s": .01},
                              "policy": {"base_url": "http://test/v1", "model": "test"},
                              "planner": {"base_url": "http://test/v1", "model": "test"}})

        async def policy(*args):
            return [{"type": "key", "key": str(runner.step_number), "modifiers": []}], "", None

        async def planner(*args):
            return "Continue the task"

        async def review(*args, **kwargs):
            return {"status": "achieved", "observation": "Test objective is visible", "next_objective": ""}

        async def settled():
            return screen.capture_image()

        runner._policy_actions = policy
        runner._plan_intent = planner
        runner._assess_outcome = review
        runner._settled_image = settled
        return runner

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.005)

    async def test_auto_executes_multiple_steps_and_snapshot_replays(self):
        runner = self.runner()
        runner.start("Build a part", "auto")
        await runner._task
        self.assertEqual(len(runner.session.screen.actions), 3)
        snapshot = runner.snapshot()
        self.assertFalse(snapshot["active"])
        self.assertEqual(snapshot["completed_steps"], 3)
        self.assertEqual(len([e for e in snapshot["events"] if e["t"] == "step_done"]), 3)
        self.assertEqual(len({e["id"] for e in snapshot["events"]}), len(snapshot["events"]))

    async def test_switch_manual_to_auto_wakes_waiting_runner(self):
        runner = self.runner()
        runner.start("First objective", "manual")
        await self.until(lambda: runner.phase == "awaiting")
        self.assertEqual(len(runner.session.screen.actions), 1)
        runner.set_mode("auto")
        await asyncio.wait_for(runner._task, 3)
        self.assertEqual(len(runner.session.screen.actions), 3)

    async def test_pause_stops_at_step_boundary_and_resume_continues(self):
        runner = self.runner()
        runner.start("Build", "auto")
        runner.pause(True)
        await self.until(lambda: runner.phase == "paused")
        self.assertEqual(runner.session.screen.actions, [])
        runner.pause(False)
        await runner._task
        self.assertEqual(len(runner.session.screen.actions), 3)

    async def test_stop_cancels_inflight_http_without_waiting_for_timeout(self):
        runner = self.runner()
        entered = asyncio.Event()

        async def blocked_post(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def planner(*args):
            return await runner._chat(runner.config["policy"], [])

        runner._plan_intent = planner
        with patch.object(httpx.AsyncClient, "post", blocked_post):
            runner.start("Build", "auto")
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(runner.wait_stopped(), .5)
        self.assertFalse(runner.active)
        self.assertEqual(runner.session.screen.actions, [])

    async def test_stop_before_action_injection(self):
        runner = self.runner()
        runner.config["agent"]["action_delay_s"] = 2
        runner.start("Build", "auto")
        await self.until(lambda: any(e["t"] == "action" for e in runner.events))
        await asyncio.wait_for(runner.wait_stopped(), .5)
        self.assertEqual(runner.session.screen.actions, [])

    async def test_new_run_drops_queued_intents(self):
        runner = self.runner()
        runner.start("Build", "manual")
        runner.submit_intent("Stale objective")
        await runner.wait_stopped()
        runner.start("New task", "manual", new_task=True)
        await self.until(lambda: runner.phase == "awaiting")
        intents = [e["text"] for e in runner.events if e["t"] == "intent"]
        self.assertEqual(intents, ["Continue the task"])
        self.assertEqual(list(runner.user_messages), ["New task"])
        await runner.wait_stopped()

    async def test_empty_model_output_does_not_claim_completion(self):
        runner = self.runner()

        async def empty_post(*args, **kwargs):
            return httpx.Response(200, request=httpx.Request("POST", "http://test"),
                                  json={"choices": [{"message": {"content": ""}}]})

        with patch.object(httpx.AsyncClient, "post", empty_post):
            with self.assertRaisesRegex(ValueError, "empty response"):
                await runner._chat(runner.config["policy"], [])

    async def test_slow_subscriber_gets_snapshot(self):
        runner = self.runner()
        queue = runner.subscribe()
        for index in range(201):
            runner.emit({"t": "note", "message": str(index)})
        self.assertEqual((await queue.get())["t"], "snapshot")
        self.assertIn(queue, runner.subscribers)

    async def test_repeated_actions_on_unchanged_screen_pause_for_review(self):
        runner = self.runner(max_steps=10)
        async def repeat(*args):
            return [{"type": "key", "key": "Enter", "modifiers": []}], "", None
        runner._policy_actions = repeat
        runner.start("Build", "auto")
        await self.until(lambda: runner.phase == "paused")
        self.assertEqual(len(runner.session.screen.actions), 2)
        self.assertTrue(any("Screen changes do not prove progress" in e.get("message", "") for e in runner.events))
        await runner.wait_stopped()

    async def test_blank_startup_frame_is_not_sent_to_model(self):
        runner = self.runner()
        runner.session.screen.capture_image = lambda: Image.new("RGB", (100, 100))
        runner.start("Build", "auto")
        await self.until(lambda: runner.phase == "waiting_screen")
        self.assertEqual(runner.session.screen.actions, [])
        await asyncio.wait_for(runner.wait_stopped(), .5)

    async def test_policy_schema_and_feedback_correct_missing_coordinate(self):
        runner = self.runner()
        runner.config["policy"]["strict"] = True
        calls = []

        async def chat(endpoint, messages, **extra):
            calls.append(([dict(m) for m in messages], extra))
            if len(calls) == 1:
                return '{"actions":[{"type":"click","x":123,"button":"left"}]}'
            return '{"actions":[{"type":"click","x":123,"y":456,"button":"left"}]}'

        runner._chat = chat
        actions, _, error = await AgentRunner._policy_actions(runner, "Click", "image")
        self.assertIsNone(error)
        self.assertEqual(actions[0]["y"], 456)
        self.assertEqual(calls[0][1]["response_format"]["type"], "json_schema")
        self.assertIn("Validation failed", calls[1][0][-1]["content"])
        self.assertIn("'y'", calls[1][0][-1]["content"])
        self.assertIn('"type":"click"', calls[0][0][0]["content"])
        self.assertEqual(runner.session.screen.actions, [])

    async def test_truncated_response_is_retried_not_repaired(self):
        runner = self.runner()
        runner.config["policy"]["strict"] = False
        attempts = 0

        async def chat(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise TruncatedCompletion('{"actions":[{"type":"key","key":"n","modifiers":[]}]}')

        runner._chat = chat
        actions, _, error = await AgentRunner._policy_actions(runner, "Click", "image")
        self.assertIsNone(actions)
        self.assertIn("token limit", error)
        self.assertEqual(attempts, 3)

    def test_stock_wrapper_repair_preserves_coordinates_and_rejects_incomplete(self):
        self.assertEqual(repair_completion('[{"click":[216,169],"button":"left"}]'),
                         [{"type": "click", "x": 216, "y": 169, "button": "left"}])
        self.assertIsNone(repair_completion('{"actions":[{"type":"click","x":12,"button":"left"}]}'))
        self.assertIsNone(repair_completion('[{"click":[-1,169],"button":"left"}]'))
        self.assertIsNone(repair_completion('[{"click":[NaN,169],"button":"left"}]'))

    async def test_invalid_policy_stops_after_three_attempts_without_replanning(self):
        runner = self.runner()
        runner._policy_actions = AgentRunner._policy_actions.__get__(runner)
        attempts = 0

        async def chat(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return '{"actions":[{"type":"click","x":12,"button":"left"}]}'

        runner._chat = chat
        runner.start("Build", "auto")
        await asyncio.wait_for(runner._task, 3)
        self.assertEqual(attempts, 3)
        self.assertEqual(runner.step_number, 1)
        self.assertEqual(runner.session.screen.actions, [])
        self.assertFalse(runner.active)
        self.assertTrue(any(e["t"] == "error" for e in runner.events))

    async def test_chat_detects_length_finish_even_when_content_looks_complete(self):
        runner = self.runner()

        async def post(*args, **kwargs):
            return httpx.Response(200, request=httpx.Request("POST", "http://test"),
                                  json={"choices": [{"finish_reason": "length", "message": {
                                      "content": '{"actions":[{"type":"key","key":"n","modifiers":[]}]}'}}]})

        with patch.object(httpx.AsyncClient, "post", post):
            with self.assertRaises(TruncatedCompletion):
                await runner._chat(runner.config["policy"], [])

    def test_exact_numeric_string_coordinate_can_be_repaired(self):
        self.assertEqual(repair_completion('{"actions":[{"type":"click","x":12,"y":"345","button":"left"}]}')[0]["y"], 345)
        self.assertIsNone(repair_completion('{"actions":[{"type":"click","x":12,"y":"345px","button":"left"}]}'))

    def test_coordinate_repair_preserves_pointer_semantics(self):
        action = repair_completion('{"actions":[{"type":"click","x":12,"y":"345","button":"left","modifiers":["ctrl"],"clicks":2}]}')[0]
        self.assertEqual(action["modifiers"], ["ctrl"])
        self.assertEqual(action["clicks"], 2)

    async def test_execution_error_feedback_preserves_completed_actions(self):
        runner = self.runner(max_steps=1)
        runner._policy_actions = AgentRunner._policy_actions.__get__(runner)
        calls = []
        runner.session.screen.capture_image = lambda: Image.new(
            "RGB", (100, 100), "#888888" if runner.session.screen.actions else "#444444")
        def execute(action):
            if action["key"] == "bad":
                raise ValueError("unknown keysym 'bad'")
            runner.session.screen.actions.append(action["key"])
        runner._execute = execute

        async def chat(endpoint, messages, **kwargs):
            calls.append(messages)
            keys = ["a", "bad", "z"] if len(calls) == 1 else ["Enter"]
            import json
            return json.dumps({"actions": [{"type": "key", "key": k, "modifiers": []} for k in keys]})
        runner._chat = chat
        runner.start("Build", "auto")
        await asyncio.wait_for(runner._task, 3)
        self.assertEqual(runner.session.screen.actions, ["a", "Enter"])
        feedback = calls[1][-1]["content"]
        self.assertIn("unknown keysym", feedback)
        self.assertIn("completed_actions", feedback)
        self.assertIn("FRESH screenshot", feedback)
        def image_url(messages):
            return next(part["image_url"]["url"] for message in messages
                        if isinstance(message["content"], list)
                        for part in message["content"] if part["type"] == "image_url")
        self.assertNotEqual(image_url(calls[0]), image_url(calls[1]))
        self.assertIsNone(runner.execution_feedback)

    async def test_manual_execution_errors_retry_same_objective_then_stop(self):
        runner = self.runner()
        intents = []
        async def policy(intent, screenshot):
            intents.append(intent)
            return [{"type": "key", "key": "bad", "modifiers": []}], "", None
        def execute(action):
            raise ValueError("unsupported key")
        runner._policy_actions = policy
        runner._execute = execute
        runner.start("Open the sketch", "manual")
        await asyncio.wait_for(runner._task, 3)
        self.assertEqual(intents, ["Continue the task"] * 3)
        self.assertEqual(runner.task_text, "Open the sketch")
        self.assertEqual(runner.completed_steps, 0)
        self.assertFalse(runner.active)
        self.assertTrue(any("failed three times" in e.get("message", "") for e in runner.events))

    async def test_body_creation_loop_is_blocked_despite_changing_screen_and_positive_reviews(self):
        runner = self.runner(max_steps=40)
        calls = []
        runner._policy_actions = AgentRunner._policy_actions.__get__(runner)
        runner._plan_intent = AgentRunner._plan_intent.__get__(runner)
        runner.session.screen.capture_image = lambda: Image.new("RGB", (100, 100),
            (40 + 30 * len(runner.session.screen.actions), 50, 50))
        runner._execute = lambda a: runner.session.screen.actions.append(a)
        async def chat(endpoint, messages, **kwargs):
            calls.append(messages)
            if kwargs.get("response_format", {}).get("json_schema", {}).get("name") == "cad_plan":
                return json.dumps({"decision":"act", "objective":"Activate the rectangle tool", "expected_result":"Rectangle tool active", "message":"", "wait_seconds":0})
            return '{"actions":[{"type":"click","x":146,"y":72,"button":"left"}]}'
        runner._chat = chat
        runner.start("Make a plate", "auto")
        await self.until(lambda: runner.phase == "paused")
        self.assertEqual(len(runner.session.screen.actions), 2)
        self.assertIn("repeated_actions", json.dumps(calls[-1]))
        self.assertIn("repeated_actions", json.dumps(calls[-2]))  # planner as well as policy
        self.assertEqual(runner.completed_steps, 2)
        await runner.wait_stopped()

    async def test_wrong_outcomes_trigger_recovery_then_pause_and_user_can_steer(self):
        runner = self.runner(max_steps=6)
        async def blocked(*args, **kwargs):
            return {"status": "blocked", "observation": "A new Body appeared, but no sketch editor opened", "next_objective": "Use the labeled New Sketch command"}
        runner._assess_outcome = blocked
        runner.start("Create a sketch", "manual")
        await self.until(lambda: runner.phase == "paused")
        self.assertEqual(runner.completed_steps, 4)
        self.assertIn("new Body", runner._supervision_context())
        runner.submit_intent("Open the Part Design menu")
        await self.until(lambda: runner.completed_steps >= 5)
        self.assertIn("Open the Part Design menu", runner.user_messages)
        self.assertFalse(any(e["t"] == "intent" and e["text"] == "Open the Part Design menu" for e in runner.events))
        await runner.wait_stopped()

    async def test_partial_progress_continues_with_review_guided_next_objective(self):
        runner = self.runner(max_steps=2)
        calls = []
        async def policy(intent, screenshot):
            calls.append(intent)
            return [{"type": "key", "key": str(len(calls)), "modifiers": []}], "", None
        async def review(*args, **kwargs):
            return {"status": "progress" if len(calls) == 1 else "achieved", "observation": "Sketch input state", "next_objective": "Place the other corner"}
        async def planner(*args):
            return runner.review_history[-1]["next_objective"] if runner.review_history else "Activate the rectangle tool"
        runner._plan_intent = planner
        runner._policy_actions, runner._assess_outcome = policy, review
        runner.start("Draw a rectangle", "manual")
        await asyncio.wait_for(runner._task, 3)
        self.assertEqual(calls, ["Activate the rectangle tool", "Place the other corner"])

    async def test_planner_done_is_not_accepted_without_task_review(self):
        runner = self.runner()
        async def planner(*args):
            return "DONE"
        async def review(*args, **kwargs):
            self.assertTrue(kwargs["whole_task"])
            return {"status": "uncertain", "observation": "Dimensions are not visible", "next_objective": "Inspect dimensions"}
        runner._plan_intent, runner._assess_outcome = planner, review
        runner.start("Make the plate", "auto")
        await self.until(lambda: runner.phase == "paused")
        self.assertFalse(any(e["t"] == "done" for e in runner.events))
        self.assertEqual(runner.completed_steps, 0)
        await runner.wait_stopped()

    async def test_visual_review_uses_two_images_and_corrects_invalid_review(self):
        runner = self.runner()
        calls = []
        async def chat(endpoint, messages, **kwargs):
            calls.append(list(messages))
            if len(calls) == 1:
                return '{"status":"achieved"}'
            return '{"status":"blocked","observation":"A Body, not a sketch, was created","next_objective":"Use New Sketch"}'
        runner._chat = chat
        result = await AgentRunner._assess_outcome(runner, "Create sketch", "before", "after", [])
        self.assertEqual(result["status"], "blocked")
        images = [p["image_url"]["url"] for p in calls[0][1]["content"] if p["type"] == "image_url"]
        self.assertEqual(images, ["data:image/jpeg;base64,before", "data:image/jpeg;base64,after"])
        self.assertIn("Review validation failed", calls[1][-1]["content"])

    async def test_review_failure_is_uncertain_never_success(self):
        runner = self.runner()
        async def chat(*args, **kwargs):
            raise RuntimeError("Endpoint unavailable")
        runner._chat = chat
        result = await AgentRunner._assess_outcome(runner, "Create sketch", "before", "after", [])
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("Endpoint unavailable", result["observation"])

    async def test_stop_cancels_visual_review(self):
        runner = self.runner()
        runner._assess_outcome = AgentRunner._assess_outcome.__get__(runner)
        entered = asyncio.Event()
        async def post(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(runner, "_stream_model", post):
            runner.start("Draw", "auto")
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(runner.wait_stopped(), .5)
        self.assertEqual(runner.completed_steps, 1)

    async def test_stock_repair_cannot_bypass_one_action_review_budget(self):
        runner = self.runner()
        runner.config["agent"]["max_actions_per_step"] = 1
        runner.config["policy"]["strict"] = False
        calls = []
        async def chat(endpoint, messages, **kwargs):
            calls.append(kwargs)
            return '{"actions":[{"type":"key","key":"a","modifiers":[]},{"type":"key","key":"b","modifiers":[]}]}'
        runner._chat = chat
        actions, raw, error = await AgentRunner._policy_actions(runner, "Create sketch", "image")
        self.assertIsNone(actions)
        self.assertIn("at most 1", error)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["response_format"]["json_schema"]["schema"]["properties"]["actions"]["maxItems"], 1)

    async def test_visual_completion_never_claims_native_geometry_verified(self):
        runner = self.runner()
        async def planner(*args):
            return "DONE"
        runner._plan_intent = planner
        runner.start("Build", "auto")
        await asyncio.wait_for(runner._task, 3)
        done = next(e for e in runner.events if e["t"] == "done")
        self.assertFalse(done["verified"])
        self.assertIn("unverified", done["reason"])


if __name__ == "__main__":
    unittest.main()
