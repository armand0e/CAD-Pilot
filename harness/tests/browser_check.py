"""Browser integration check against a local harness (no model requests)."""
import json
import io
import time
from contextlib import ExitStack
import httpx
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from PIL import Image

ARTIFACTS = Path(__file__).resolve().parents[2] / "runs/harness-checks"


def main():
    errors = []
    sent_inputs = []
    with ExitStack() as cleanup, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        def expose_test_handler(route):
            response = route.fetch()
            route.fulfill(response=response, body=response.text() + "\nwindow.__testAgentEvent = handleAgentEvent;\n")
        page.route("**/static/app.js", expose_test_handler)
        page.on("pageerror", lambda error: errors.append(str(error)))
        def watch_socket(socket):
            if "/ws/view/" in socket.url:
                socket.on("framesent", lambda payload: sent_inputs.append(json.loads(payload)))
        page.on("websocket", watch_socket)
        page.goto("http://127.0.0.1:7802", wait_until="networkidle")
        page.locator(".app-tile").first.wait_for()
        assert page.locator("#btn-send").is_disabled()
        page.screenshot(path=str(ARTIFACTS / "workspace-desktop.png"))
        page.locator("[data-prompt]").first.click()
        assert "60" in page.locator("#composer-input").input_value()
        page.locator("#btn-help").click()
        assert page.locator("#help-dialog").is_visible()
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(ARTIFACTS / "workspace-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1440, "height": 900})
        # Real CAD display and frame stream, isolated from the user's sessions.
        page.locator(".app-tile", has_text="OpenSCAD").click()
        page.locator("#screen:not(.hidden)").wait_for(timeout=30000)
        expect(page.locator("#connection-status")).to_have_text("Live")
        sid = page.evaluate("localStorage.getItem('cadpilot-session')")
        assert sid
        cleanup.callback(lambda: httpx.delete(f'http://127.0.0.1:7802/api/sessions/{sid}', timeout=20))
        # Wait for CAD content, not just the first (possibly black) framebuffer.
        deadline = time.monotonic() + 30
        while True:
            frame = page.request.get(f"http://127.0.0.1:7802/api/sessions/{sid}/frame.jpg")
            histogram = Image.open(io.BytesIO(frame.body())).convert("L").histogram()
            if sum(histogram[16:]) / sum(histogram) >= .02:
                break
            assert time.monotonic() < deadline, "CAD display remained blank"
            page.wait_for_timeout(200)
        assert page.locator('#mode-toggle button.on').inner_text() == 'Auto'
        bounds = page.locator("#screen").bounding_box()
        page.mouse.move(bounds["x"] + 50, bounds["y"] + 100)
        page.locator("#screen").focus()
        page.keyboard.press("Space")
        page.keyboard.press("Shift+A")
        page.wait_for_timeout(200)
        assert any(m.get("t") == "key" and m.get("key") == " " for m in sent_inputs)
        assert any(m.get("t") == "move" for m in sent_inputs), "Hover/preselection must work without dragging"
        assert any(m.get("t") == "key" and m.get("key") == "a" and "shift" in m["modifiers"] for m in sent_inputs)
        assert not any(m.get("t") == "text" for m in sent_inputs), "Keyboard commands must not become clipboard pastes"
        page.screenshot(path=str(ARTIFACTS / "workspace-live.png"))
        page.reload(wait_until="networkidle")
        page.locator("#screen:not(.hidden)").wait_for(timeout=30000)
        assert page.evaluate("localStorage.getItem('cadpilot-session')") == sid
        # Replayed supervision events must distinguish executed input from visual results.
        page.evaluate("""() => window.__testAgentEvent({t:'snapshot', active:false, paused:false, mode:'auto',
            completed_steps:1, max_steps:40, started_at:1, phase:'idle', task:'Create a sketch', events:[
              {t:'control', locked:true, mode:'auto', task:'Create a sketch', ts:1, id:1},
              {t:'intent', step:1, text:'Open a sketch', ts:1, id:2},
              {t:'step_done', step:1, verified:false, ts:2, id:3},
              {t:'step_review', step:1, status:'blocked', observation:'A Body appeared, not a sketch <test>', ts:3, id:4},
              {t:'done', reason:'stopped by user', ts:4, id:5},
              {t:'control', locked:false, ts:4, id:6}
            ]})""")
        assert page.locator('[data-kind="cad"] .activity-status').inner_text() == "Executed · needs correction"
        assert "Body appeared" in page.locator('[data-kind="cad"] .activity-summary').text_content()
        assert page.locator('[data-kind="cad"] test').count() == 0
        assert page.locator("#run-progress").inner_text() == "1 / 40 operations this run"
        assert page.locator(".step-state.done").count() == 0
        page.screenshot(path=str(ARTIFACTS / "workspace-supervision.png"))
        page.evaluate("""() => {
          const send=window.__testAgentEvent;
          send({t:'research_start',call:1,query:'motor specification'});
          send({t:'research_result',operation:'read',sources:[{id:'web_123456789abc',url:'https://example.com/spec',title:'Motor <img onerror=alert(1)>',kind:'page',excerpt:'Mounting: 41.7 mm'}],notes:{facts:[],assumptions:['Screw engagement unverified'],unknowns:[]}});
          send({t:'assistant',message:'Dimensions are sourced [web_123456789abc].'});
          send({t:'assistant',message:'Which variant?'});
          send({t:'pause',paused:true,reason:'Which variant?'});
        }""")
        assert page.locator('[data-kind="read"]').count()==1
        assert page.locator('[data-kind="read"] img').count()==0
        page.locator('.citation').click()
        assert page.locator('.citation-preview a').last.get_attribute('href')=='https://example.com/spec'
        assert page.locator('.msg.assistant').filter(has_text='Which variant?').count()==1
        page.set_viewport_size({'width':390,'height':844})
        page.locator('[data-kind="read"] .activity-heading').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(ARTIFACTS/'workspace-research-mobile.png'))
        page.set_viewport_size({'width':1440,'height':900})
        page.locator("#btn-close-session").click()
        page.locator("#close-dialog button[value=cancel]").click()
        assert page.locator("#screen").is_visible()
        response = page.request.delete(f"http://127.0.0.1:7802/api/sessions/{sid}")
        assert response.ok
        browser.close()
    assert not errors, errors
    print(json.dumps({"browser_errors": errors, "desktop_mobile_and_live_session": "passed"}))


if __name__ == "__main__":
    main()
