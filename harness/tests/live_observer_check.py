"""Opt-in fixed-adapter check in its own FreeCAD session. No model requests."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from server.sessions import SessionManager
from server.detect import detect_apps
from server.native import read_native_state


def main():
    output = Path(tempfile.mkdtemp(prefix="observer-", dir=ROOT / "runs/harness-checks"))
    app = next(a.to_json() for a in detect_apps() if a.id == "appimage-freecad")
    app["command"] += [str(Path(__file__).with_name("observer_fixture.FCMacro"))]
    manager = SessionManager(state_root=output)
    try:
        session = manager.create(app)
        deadline = time.monotonic() + 30
        state = None
        while time.monotonic() < deadline:
            state = read_native_state(session.state_dir)
            if state and state.get("objects"):
                break
            time.sleep(.2)
        print(json.dumps({"output":str(output), "selection":state.get("selection") if state else None}), flush=True)
        assert state and not state.get("observer_error"), state
        assert state["document"]["name"] == "ObserverFixture"
        assert state["document"]["editing"] == "Sketch"
        assert state["selection"][0]["subelements"] == ["Edge1"]
        assert any("sketch" in c["label"].lower() for c in state["controls_grid_0_999"])
        assert all(0 <= c["x"] <= 999 and 0 <= c["y"] <= 999 for c in state["controls_grid_0_999"])
        sketch = next(o for o in state["objects"] if o["name"] == "Sketch")
        assert sketch["geometry_count"] == 1 and sketch["constraints"][0]["value"] == 12
        deadline = time.monotonic() + 15
        again = state
        while time.monotonic() < deadline:
            candidate = read_native_state(session.state_dir)
            if candidate and candidate["timestamp"] > state["timestamp"]:
                again = candidate
                break
            time.sleep(.2)
        # Startup can change visibility/touched flags independently of the observer.
        content = lambda items: [{k:v for k,v in item.items() if k not in {"state","visible"}} for item in items]
        assert content(again["objects"]) == content(state["objects"])
        assert again["timestamp"] > state["timestamp"]
        assert not again["native_geometry_verified"]
        # Check the actual coordinate mapping with a real, labeled control. No icon guessing.
        leave = next(c for c in again["controls_grid_0_999"] if c["label"] in {"Leave Sketch", "Close"} and c["enabled"])
        session.screen.click(round(leave["x"] * session.width / 999), round(leave["y"] * session.height / 999))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            changed = read_native_state(session.state_dir)
            if changed and changed["document"]["editing"] is None:
                break
            time.sleep(.2)
        assert changed["document"]["editing"] is None, "Native button coordinates missed"
        print(json.dumps({"native_selection_constraints_and_read_only":"passed"}))
    finally:
        if manager.sessions:
            session.screen.capture_image().save(output / "final.png")
        manager.close_all()


if __name__ == "__main__":
    main()
