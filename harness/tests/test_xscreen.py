import threading
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.xscreen import XScreen
from Xlib import X


class PointerTests(unittest.TestCase):
    def screen(self):
        screen = object.__new__(XScreen)
        screen.lock = threading.RLock()
        screen.display = SimpleNamespace(sync=lambda: None)
        screen._keysym_code = lambda name: 42
        screen.events = []
        screen.move = lambda x, y: screen.events.append(("move", x, y))
        screen.button = lambda code, down: screen.events.append(("button", code, down))
        return screen

    def test_zero_scroll_does_not_inject_down_tick(self):
        screen = self.screen()
        screen.scroll(1, 2, 0)
        self.assertFalse(any(e[0] == "button" for e in screen.events))

    def test_horizontal_and_vertical_directions(self):
        screen = self.screen()
        screen.scroll(1, 2, 2, delta_x=-1)
        self.assertEqual([e[1] for e in screen.events if e[0] == "button" and e[2]], [4, 4, 6])

    def test_drag_releases_button_and_modifiers_on_failure(self):
        screen = self.screen()
        calls = []
        def move(x, y):
            if x > 0:
                raise ValueError("injection failed")
        screen.move = move
        with patch("server.xscreen.xtest.fake_input", side_effect=lambda d, kind, code: calls.append((kind, code))):
            with self.assertRaises(ValueError):
                screen.drag(0, 0, 20, 20, modifiers=["ctrl"])
        self.assertEqual(calls, [(X.KeyPress, 42), (X.KeyRelease, 42)])
        self.assertEqual(screen.events[-1], ("button", 1, False))

    def test_ascii_cad_typing_is_not_a_clipboard_paste(self):
        screen=self.screen()
        screen.display_name=':test'
        screen.display.keysym_to_keycode=lambda value:42
        screen.display.keycode_to_keysym=lambda *args:ord('a')
        screen._key_event=lambda *args:None
        with patch.object(screen,'paste_text') as paste:
            screen.type_text('a 345')
            paste.assert_not_called()

    def test_multiline_text_uses_explicit_paste_transport(self):
        screen=self.screen();screen.display_name=':test'
        with patch.object(screen,'paste_text') as paste:
            screen.type_text('cube(3);\n')
            paste.assert_called_once_with('cube(3);\n')
