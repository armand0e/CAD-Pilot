"""Opt-in live policy smoke check; creates and closes only its own empty OpenSCAD session."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)  # test predicate evaluation
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sid = None
    try:
        page.goto("http://127.0.0.1:7802", wait_until="networkidle")
        page.locator(".app-tile", has_text="OpenSCAD").click()
        page.locator("#screen:not(.hidden)").wait_for(timeout=30000)
        page.wait_for_function("document.querySelector('#connection-status').textContent === 'Live'")
        sid = page.evaluate("localStorage.getItem('cadpilot-session')")
        page.locator("button[data-mode=manual]").click()
        page.fill("#composer-input", "Press Ctrl+N once to open a new empty document.")
        page.locator("#btn-send").click()
        page.wait_for_function("document.querySelectorAll('.step-card[data-executed=true]').length >= 1", timeout=90000)
        page.wait_for_function("document.querySelector('#run-phase').textContent === 'Waiting for your objective'")
        page.reload(wait_until="networkidle")
        page.wait_for_function("document.querySelectorAll('.step-card[data-executed=true]').length >= 1")
        assert page.locator("#btn-stop").is_visible()
        page.fill("#composer-input", "Press Ctrl+N once to open a new empty document.")
        page.locator("#btn-send").click()
        page.wait_for_function("document.querySelectorAll('.step-card[data-executed=true]').length >= 2", timeout=90000)
        page.locator("#btn-pause").click()
        page.wait_for_function("document.querySelector('#run-phase').textContent.startsWith('Paused')")
        page.screenshot(path=str(ROOT / "runs/harness-checks/workspace-agent.png"))
        page.locator("#btn-takeover").click()
        page.locator("#btn-stop").wait_for(state="hidden", timeout=2000)
        snapshot = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").json()
        assert not snapshot["active"]
        assert snapshot["completed_steps"] >= 2
        assert not errors, errors
        print(json.dumps({"live_model": "passed", "steps": snapshot["completed_steps"], "reload_pause_stop": "passed", "browser_errors": errors}))
    except Exception:
        page.screenshot(path=str(ROOT / "runs/harness-checks/live-failure.png"))
        if sid:
            print("FAILURE_STATE", page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/activity").text())
        print("BROWSER_ERRORS", errors)
        raise
    finally:
        if sid:
            page.request.delete(f"http://127.0.0.1:7802/api/sessions/{sid}")
        browser.close()
