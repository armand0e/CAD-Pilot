"""Capture frames from and inject input into one X display (pure python-xlib, no system tools).

One :class:`XScreen` per session. All Xlib calls are serialized with a lock: python-xlib
connections are not thread-safe and the streamer, the user's input events and the agent's
executor all touch the same display.
"""

from __future__ import annotations

import io
import threading
import time
from contextlib import contextmanager

from PIL import Image
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest
from Xlib.protocol import event as xevent
from .clipboard import DesktopClipboard, ClipboardUnavailable

BUTTON_CODES = {"left": 1, "middle": 2, "right": 3, "wheel_up": 4, "wheel_down": 5}
KEYSYM_NAMES = {
    "Enter": "Return", "Escape": "Escape", "Backspace": "BackSpace", "Tab": "Tab", "Delete": "Delete",
    "Space": "space", "Home": "Home", "End": "End", "PageUp": "Prior", "PageDown": "Next",
    "ArrowLeft": "Left", "ArrowRight": "Right", "ArrowUp": "Up", "ArrowDown": "Down",
    "Insert": "Insert", "ContextMenu": "Menu", "PrintScreen": "Print", "CapsLock": "Caps_Lock",
    "NumLock": "Num_Lock", "ScrollLock": "Scroll_Lock", "Meta": "Super_L", "Control": "Control_L",
    "Shift": "Shift_L", "Alt": "Alt_L", "Pause": "Pause",
}
MODIFIER_KEYSYMS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "meta": "Super_L"}


class XScreen:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name
        self.display = xdisplay.Display(display_name)
        self.root = self.display.screen().root
        geometry = self.root.get_geometry()
        self.width, self.height = geometry.width, geometry.height
        self.lock = threading.RLock()
        self._fitted_windows: set[int] = set()
        self._pressed_keys = set()
        self._pressed_buttons = set()
        self._manual_modifiers = {}
        self._clipboard = None

    def has_window_manager(self):
        with self.lock:
            return bool(self.root.get_full_property(self.display.intern_atom("_NET_SUPPORTING_WM_CHECK"), X.AnyPropertyType))

    def window_titles(self):
        """Read-only window identity for fixed native-file open acknowledgments."""
        with self.lock:
            clients = self.root.get_full_property(self.display.intern_atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
            titles = []
            for wid in clients.value if clients else []:
                try:
                    window = self.display.create_resource_object('window', int(wid))
                    if window.get_attributes().map_state != X.IsViewable:
                        continue
                    prop = window.get_full_property(self.display.intern_atom('_NET_WM_NAME'), X.AnyPropertyType)
                    titles.append(bytes(prop.value).decode('utf-8', errors='replace') if prop else window.get_wm_name() or '')
                except Exception:
                    continue
            return titles

    def _fit_new_windows(self) -> None:
        """Give normal application windows the canvas; preserve dialog geometry.

        Called with the display lock held. Without a window manager, configuring the
        client directly is necessary; with one, use the standard maximize request.
        """
        normal = self.display.intern_atom("_NET_WM_WINDOW_TYPE_NORMAL")
        kind_atom = self.display.intern_atom("_NET_WM_WINDOW_TYPE")
        wm = self.root.get_full_property(self.display.intern_atom("_NET_SUPPORTING_WM_CHECK"), X.AnyPropertyType)
        if wm:
            clients = self.root.get_full_property(self.display.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
            windows = [self.display.create_resource_object("window", int(w)) for w in clients.value] if clients else []
        else:
            windows = self.root.query_tree().children
        for window in windows:
            if window.id in self._fitted_windows:
                continue
            try:
                if window.get_attributes().map_state != X.IsViewable:
                    continue
                kinds = window.get_full_property(kind_atom, X.AnyPropertyType)
                if not kinds or normal not in kinds.value or window.get_wm_transient_for():
                    continue
                if wm:
                    request = xevent.ClientMessage(window=window.id,
                        client_type=self.display.intern_atom("_NET_WM_STATE"),
                        data=(32, [1, self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ"),
                                   self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT"), 1, 0]))
                    self.root.send_event(request, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                else:
                    window.configure(x=0, y=0, width=self.width, height=self.height)
                self._fitted_windows.add(window.id)
                self.display.sync()
            except Exception:
                # A window can disappear between the tree query and its property read.
                continue

    # ---- capture ------------------------------------------------------------------------
    def capture_image(self) -> Image.Image:
        with self.lock:
            self._fit_new_windows()
            raw = self.root.get_image(0, 0, self.width, self.height, X.ZPixmap, 0xFFFFFFFF)
        return Image.frombytes("RGB", (self.width, self.height), raw.data, "raw", "BGRX")

    def capture(self, *, quality: int = 80, max_width: int | None = None) -> bytes:
        image = self.capture_image()
        if max_width and image.width > max_width:
            image = image.resize((max_width, round(image.height * max_width / image.width)))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        return buffer.getvalue()

    # ---- input --------------------------------------------------------------------------
    def move(self, x: int, y: int) -> None:
        with self.lock:
            xtest.fake_input(self.display, X.MotionNotify, x=max(0, min(self.width - 1, int(x))), y=max(0, min(self.height - 1, int(y))))
            self.display.sync()

    def button(self, code: int, press: bool) -> None:
        with self.lock:
            if code not in range(1, 8):
                raise ValueError("Unsupported desktop mouse button")
            if press:
                self._pressed_buttons.add(code)
            xtest.fake_input(self.display, X.ButtonPress if press else X.ButtonRelease, code)
            if not press:
                self._pressed_buttons.discard(code)
            self.display.sync()

    def _key_event(self, code: int, press: bool) -> None:
        if not hasattr(self, "_pressed_keys"):
            self._pressed_keys = set()
        if press:
            self._pressed_keys.add(code)
        xtest.fake_input(self.display, X.KeyPress if press else X.KeyRelease, code)
        if not press:
            self._pressed_keys.discard(code)

    @contextmanager
    def _held_modifiers(self, modifiers):
        with self.lock:
            codes = [self._keysym_code(MODIFIER_KEYSYMS[m]) for m in sorted(set(modifiers or []))]
            pressed = []
            try:
                for code in codes:
                    pressed.append(code)
                    self._key_event(code, True)
                yield
            finally:
                for code in reversed(pressed):
                    self._key_event(code, False)
                self.display.sync()

    def click(self, x: int, y: int, button: str = "left", *, clicks: int = 1, modifiers=None) -> None:
        with self._held_modifiers(modifiers):
            self.move(x, y)
            code = BUTTON_CODES[button]
            for _ in range(clicks):
                try:
                    self.button(code, True)
                finally:
                    self.button(code, False)

    def scroll(self, x: int, y: int, delta_y: int, *, delta_x: int = 0, modifiers=None) -> None:
        with self._held_modifiers(modifiers):
            self.move(x, y)
            for delta, negative, positive in ((delta_y, 5, 4), (delta_x, 6, 7)):
                for _ in range(abs(int(delta))):
                    self.button(positive if delta > 0 else negative, True)
                    self.button(positive if delta > 0 else negative, False)

    def _keysym_code(self, name: str) -> int:
        keysym = XK.string_to_keysym(KEYSYM_NAMES.get(name, name))
        if keysym == 0 and len(name) == 1:
            keysym = ord(name)
        if keysym == 0:
            raise ValueError(f"unknown keysym {name!r}")
        code = self.display.keysym_to_keycode(keysym)
        if code == 0:
            raise ValueError(f"keysym {name!r} not mapped on this display")
        return code

    def key(self, name: str, modifiers: list[str] | None = None) -> None:
        with self.lock:
            main = self._keysym_code(name)
            with self._held_modifiers(modifiers):
                try:
                    self._key_event(main, True)
                finally:
                    self._key_event(main, False)
                    self.display.sync()

    def paste_text(self, text: str) -> None:
        if not isinstance(text, str) or not 0 < len(text) <= 8192 or "\x00" in text:
            raise ValueError("Text must contain 1..8192 characters without NUL")
        if hasattr(self, "display_name"):
            try:
                if self._clipboard is None:
                    self._clipboard = DesktopClipboard(self.display_name)
                self._clipboard.paste(text, lambda: self.key("v", ["ctrl"]))
                return
            except ClipboardUnavailable:
                pass  # No input was sent; preserve non-text clipboards with key fallback.
        self.type_text(text, allow_paste=False)

    def type_text(self, text: str, *, allow_paste=True) -> None:
        if not isinstance(text, str) or not 0 < len(text) <= 8192 or "\x00" in text:
            raise ValueError("Text must contain 1..8192 characters without NUL")
        # Recorded CAD typing includes tool accelerators, Space and numeric viewport input.
        # Ctrl+V is not equivalent. Only multiline/Unicode editor text needs a clipboard.
        if allow_paste and hasattr(self, 'display_name') and (not text.isascii() or '\n' in text or '\r' in text):
            return self.paste_text(text)
        with self.lock:
            # Validate the whole string first: unsupported Unicode must not leave half a
            # filename, dimension, or script typed into the user's document.
            strokes = []
            for character in text.replace("\r\n", "\n"):
                special = {"\n": "Enter", "\r": "Enter", "\t": "Tab"}.get(character)
                if special:
                    strokes.append((self._keysym_code(special), False))
                    continue
                keysym = XK.string_to_keysym(character) or ord(character)
                code = self.display.keysym_to_keycode(keysym)
                if code == 0:
                    raise ValueError(f"Character {character!r} is not mapped on this desktop keyboard")
                strokes.append((code, self.display.keycode_to_keysym(code, 0) != keysym))
            for code, needs_shift in strokes:
                with self._held_modifiers(["shift"] if needs_shift else []):
                    try:
                        self._key_event(code, True)
                    finally:
                        self._key_event(code, False)
                        self.display.sync()

    def drag(self, x: int, y: int, end_x: int, end_y: int, button: str = "left", *, steps: int = 10, modifiers=None) -> None:
        with self._held_modifiers(modifiers):
            self.move(x, y)
            code = BUTTON_CODES[button]
            self.button(code, True)
            try:
                for i in range(1, steps + 1):
                    self.move(round(x + (end_x - x) * i / steps), round(y + (end_y - y) * i / steps))
                    time.sleep(.015)
            finally:
                self.button(code, False)

    def pointer_down(self, x, y, button="left", modifiers=None):
        with self.lock:
            code = BUTTON_CODES[button]
            if code in self._manual_modifiers:
                self.pointer_up(x, y, button)
            codes = [self._keysym_code(MODIFIER_KEYSYMS[m]) for m in sorted(set(modifiers or []))]
            self._manual_modifiers[code] = codes
            try:
                for keycode in codes:
                    self._key_event(keycode, True)
                self.move(x, y)
                self.button(code, True)
            except Exception:
                self.release_inputs()
                raise

    def pointer_up(self, x, y, button="left"):
        with self.lock:
            code = BUTTON_CODES[button]
            try:
                self.move(x, y)
            finally:
                try:
                    self.button(code, False)
                finally:
                    codes = self._manual_modifiers.pop(code, [])
                    still_held = {key for keys in self._manual_modifiers.values() for key in keys}
                    for key in reversed(codes):
                        if key not in still_held:
                            self._key_event(key, False)
                    self.display.sync()

    def release_inputs(self):
        with self.lock:
            for button in list(self._pressed_buttons):
                self.button(button, False)
            for key in list(self._pressed_keys):
                self._key_event(key, False)
            self._manual_modifiers.clear()
            self.display.sync()

    def close(self) -> None:
        try:
            if self._clipboard:
                self._clipboard.close()
            self.display.close()
        except Exception:  # noqa: BLE001
            pass
