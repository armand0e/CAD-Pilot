"""Opt-in end-to-end native-file benchmark, not a scripted CAD demonstration.

The stock planner/policy receive only a natural-language task and a blank CAD app.
They must create and save the file through the harness. A separate sandboxed grader
checks solid validity, dimensions and holes. Every test creates/closes only its own session.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
from cad_policy.cad_environment import CADEnvironment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=["freecad", "openscad"], default="openscad")
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix=f"native-{args.app}-", dir=ROOT / "runs/harness-checks"))
    work = output / "work"
    work.mkdir()
    task = {"app":args.app, "length":60, "width":40, "height":8, "holes":True, "radius":2.5,
            "offset":8, "artifact":"submission.scad" if args.app == "openscad" else "submission.FCStd"}
    prompt = ("Create a 60 × 40 × 8 mm rectangular plate with four 5 mm diameter through mounting holes, "
              "each centered 8 mm from its adjacent edges. The plate starts at (0,0,0) and extends along "
              "positive X/Y/Z; holes pass along Z. Use exact dimensions. "
              f"Save a new native document at {work / task['artifact']}. Do not overwrite any existing file.")
    if args.app == "openscad":
        prompt += " Use an editable parametric OpenSCAD script and render the result."
    print(json.dumps({"output":str(output), "task":prompt}), flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width":1440,"height":900}, bypass_csp=True)
        sid = None
        try:
            page.goto("http://127.0.0.1:7802", wait_until="networkidle")
            page.locator(".app-tile", has_text="FreeCAD" if args.app == "freecad" else "OpenSCAD").click()
            page.locator("#screen:not(.hidden)").wait_for(timeout=30000)
            page.wait_for_function("document.querySelector('#connection-status').textContent === 'Live'")
            sid = page.evaluate("localStorage.getItem('cadpilot-session')")
            # Keep this older benchmark GUI-only; native-tool competence is tested separately.
            response = page.request.post(f'http://127.0.0.1:7802/api/sessions/{sid}/project', data={'operation':'engine','engine':'visual'})
            assert response.ok, response.text()
            page.fill("#composer-input", prompt)
            page.locator("#btn-send").click()
            last_id = 0
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                snapshot = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").json()
                for event in snapshot["events"]:
                    if event["id"] > last_id and event["t"] in {"intent","step_review","pause","assistant","error","done"}:
                        print(json.dumps(event), flush=True)
                last_id = max((e["id"] for e in snapshot["events"]), default=0)
                if snapshot["events"] and (not snapshot["active"] or snapshot["paused"]):
                    break
                page.wait_for_timeout(1000)
            if snapshot["active"]:
                page.locator("#btn-takeover").click()
                page.locator("#btn-stop").wait_for(state="hidden", timeout=3000)
            snapshot = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").json()
            (output / "activity.json").write_text(json.dumps(snapshot, indent=2))
            page.screenshot(path=str(output / "workspace.png"))
            (output / "frame.jpg").write_bytes(page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/frame.jpg").body())
        finally:
            if sid:
                page.request.delete(f"http://127.0.0.1:7802/api/sessions/{sid}")
            browser.close()
    env = object.__new__(CADEnvironment)
    env.task, env.directory, env.work = task, output, work
    result = env.reward()
    (output / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"output":str(output), "native_result":result}), flush=True)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
