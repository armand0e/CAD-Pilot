"""Exact Unicode/multiline text and clipboard-preservation check in a disposable OpenSCAD."""
import json
import sys
import tempfile
import time
from pathlib import Path

from Xlib import X, display

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from server.sessions import SessionManager
from server.detect import detect_apps
from server.observation import has_application_content


def clipboard_text(display_name):
    connection = display.Display(display_name)
    window = connection.screen().root.create_window(0,0,1,1,0,X.CopyFromParent)
    selection, target, prop = [connection.intern_atom(n) for n in ("CLIPBOARD","UTF8_STRING","TEST_CLIPBOARD")]
    window.convert_selection(selection,target,prop,X.CurrentTime)
    connection.flush()
    deadline = time.monotonic() + 2
    try:
        while time.monotonic() < deadline:
            if connection.pending_events():
                event = connection.next_event()
                if event.type == X.SelectionNotify:
                    value = window.get_full_property(prop,X.AnyPropertyType)
                    window.delete_property(prop)
                    connection.flush()
                    return bytes(value.value).decode() if value else None
            time.sleep(.02)
        raise AssertionError("Clipboard read timed out")
    finally:
        connection.close()


def main():
    output = Path(tempfile.mkdtemp(prefix="input-",dir=ROOT / "runs/harness-checks"))
    path = output / "text.scad"
    path.write_text("")
    app = next(a.to_json() for a in detect_apps() if a.id == "appimage-openscad")
    app["command"] += [str(path)]
    manager = SessionManager(state_root=output / "sessions")
    try:
        session = manager.create(app)
        deadline = time.monotonic() + 20
        while not has_application_content(session.screen.capture_image()):
            assert time.monotonic() < deadline
            time.sleep(.2)
        time.sleep(1)
        screen = session.screen
        screen.type_text("prior clipboard text")
        screen.key("a",["ctrl"])
        screen.key("c",["ctrl"])
        time.sleep(.2)
        assert clipboard_text(session.display) == "prior clipboard text"
        expected = "// café 🛠\nsize = [12, 8, 3];\ncube(size);\n"
        screen.type_text(expected)
        time.sleep(.4)
        screen.key("s",["ctrl"])
        deadline = time.monotonic() + 3
        while path.read_text() != expected and time.monotonic() < deadline:
            time.sleep(.1)
        screen.capture_image().save(output / "final.png")
        print(json.dumps({"debug_output":str(output), "clipboard":clipboard_text(session.display)}), flush=True)
        assert path.read_text() == expected, repr(path.read_text())
        assert clipboard_text(session.display) == "prior clipboard text"
        print(json.dumps({"exact_unicode_multiline_paste":"passed", "prior_clipboard_preserved":"passed", "output":str(output)}))
    finally:
        manager.close_all()


if __name__ == "__main__":
    main()
