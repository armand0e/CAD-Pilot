"""Bounded live FreeCAD supervision probe; never touches an existing session.

This measures runtime observations/recovery, NOT plate completion or geometry accuracy.
Artifacts include the full event log and final screenshot. Stops after ten reviewed batches,
a safety pause, a terminal result, or five minutes; closes only the session it created.
"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ARTIFACTS = Path(__file__).resolve().parents[2] / "runs/harness-checks"
TASK = "Create a 60 × 40 × 8 mm rectangular plate with four 5 mm mounting holes, each 8 mm from the adjacent edges."


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
        sid = None
        try:
            page.goto("http://127.0.0.1:7802", wait_until="networkidle")
            page.locator(".app-tile", has_text="FreeCAD").click()
            page.locator("#screen:not(.hidden)").wait_for(timeout=30000)
            page.wait_for_function("document.querySelector('#connection-status').textContent === 'Live'")
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            page.fill("#composer-input", TASK)
            page.locator("#btn-send").click()
            deadline, last_id, started = time.monotonic() + 300, 0, False
            while time.monotonic() < deadline:
                snapshot = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").json()
                for event in snapshot["events"]:
                    if event["id"] > last_id and event["t"] in {"intent", "step_review", "step_blocked", "recovery", "error", "done"}:
                        print(json.dumps(event), flush=True)
                last_id = max((e["id"] for e in snapshot["events"]), default=0)
                started |= snapshot["active"] or bool(snapshot["events"])
                reviews = [e for e in snapshot["events"] if e["t"] == "step_review"]
                if started and (not snapshot["active"] or snapshot["paused"] or len(reviews) >= 10):
                    break
                page.wait_for_timeout(1000)
            else:
                raise AssertionError("Probe exceeded five-minute bound")
            page.screenshot(path=str(ARTIFACTS / "supervision-freecad.png"))
            if snapshot["active"]:
                page.locator("#btn-takeover").click()
                page.locator("#btn-stop").wait_for(state="hidden", timeout=3000)
            final = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").json()
            (ARTIFACTS / "supervision-freecad.json").write_text(json.dumps(final, indent=2))
            assert reviews, "No visual review completed"
            assert all(e.get("native_geometry_verified") is False for e in reviews)
            assert not any(e["t"] == "error" for e in final["events"]), "Runtime error; inspect artifact"
            print(json.dumps({"runtime_supervision": "observed", "reviewed_batches": len(reviews),
                              "safety_paused": snapshot["paused"], "plate_geometry_verified": False}), flush=True)
        finally:
            if sid:
                page.request.delete(f"http://127.0.0.1:7802/api/sessions/{sid}")
            browser.close()


if __name__ == "__main__":
    main()
