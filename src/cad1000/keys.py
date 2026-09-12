"""Stable key names and modifier decoding for recorded keyboard events.

The recorder stores platform virtual-key codes (``keyCode``) plus the produced ``characters``.
Training targets need platform-independent, executable names, so this module maps codes to a
small canonical vocabulary. Unknown codes fall back to the printable character when available.
"""

from __future__ import annotations

# Modifier bitmask. Only ``ctrl == 2`` was verified from recorded Ctrl+Z events in the
# SOLIDWORKS sample; the remaining bits follow the recorder's documented ordering and are
# flagged as inferred in the policy-view report until a workflow confirms them.
MODIFIER_BITS: dict[str, int] = {"shift": 1, "ctrl": 2, "alt": 4, "meta": 8}
MODIFIER_NAMES: tuple[str, ...] = ("ctrl", "shift", "alt", "meta")

_WINDOWS_SPECIAL: dict[int, str] = {
    8: "Backspace",
    9: "Tab",
    13: "Enter",
    16: "Shift",
    17: "Control",
    18: "Alt",
    19: "Pause",
    20: "CapsLock",
    27: "Escape",
    32: "Space",
    33: "PageUp",
    34: "PageDown",
    35: "End",
    36: "Home",
    37: "ArrowLeft",
    38: "ArrowUp",
    39: "ArrowRight",
    40: "ArrowDown",
    44: "PrintScreen",
    45: "Insert",
    46: "Delete",
    91: "Meta",
    92: "Meta",
    93: "ContextMenu",
    106: "*",
    107: "+",
    109: "-",
    110: ".",
    111: "/",
    144: "NumLock",
    145: "ScrollLock",
    186: ";",
    187: "=",
    188: ",",
    189: "-",
    190: ".",
    191: "/",
    192: "`",
    219: "[",
    220: "\\",
    221: "]",
    222: "'",
}
_WINDOWS_SPECIAL.update({48 + digit: str(digit) for digit in range(10)})
_WINDOWS_SPECIAL.update({96 + digit: str(digit) for digit in range(10)})
_WINDOWS_SPECIAL.update({65 + index: chr(ord("a") + index) for index in range(26)})
_WINDOWS_SPECIAL.update({112 + index: f"F{index + 1}" for index in range(12)})

_MAC_SPECIAL: dict[int, str] = {
    36: "Enter",
    48: "Tab",
    49: "Space",
    51: "Backspace",
    53: "Escape",
    55: "Meta",
    56: "Shift",
    57: "CapsLock",
    58: "Alt",
    59: "Control",
    65: ".",
    67: "*",
    69: "+",
    75: "/",
    76: "Enter",
    78: "-",
    96: "F5",
    97: "F6",
    98: "F7",
    99: "F3",
    100: "F8",
    101: "F9",
    103: "F11",
    109: "F10",
    111: "F12",
    115: "Home",
    116: "PageUp",
    117: "Delete",
    118: "F4",
    119: "End",
    120: "F2",
    121: "PageDown",
    122: "F1",
    123: "ArrowLeft",
    124: "ArrowRight",
    125: "ArrowDown",
    126: "ArrowUp",
}
_MAC_LETTERS = {
    0: "a", 11: "b", 8: "c", 2: "d", 14: "e", 3: "f", 5: "g", 4: "h", 34: "i", 38: "j",
    40: "k", 37: "l", 46: "m", 45: "n", 31: "o", 35: "p", 12: "q", 15: "r", 1: "s", 17: "t",
    32: "u", 9: "v", 13: "w", 7: "x", 16: "y", 6: "z",
    29: "0", 18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8", 25: "9",
    82: "0", 83: "1", 84: "2", 85: "3", 86: "4", 87: "5", 88: "6", 89: "7", 91: "8", 92: "9",
}
_MAC_SPECIAL.update(_MAC_LETTERS)

_TABLES: dict[str, dict[int, str]] = {"windows": _WINDOWS_SPECIAL, "mac": _MAC_SPECIAL}
_CONTROL_CHARACTER_NAMES = {"\r": "Enter", "\n": "Enter", "\t": "Tab", "\x1b": "Escape", "\x08": "Backspace", "\x7f": "Delete"}

MODIFIER_KEY_NAMES = {"Shift", "Control", "Alt", "Meta", "CapsLock", "NumLock", "ScrollLock"}


def key_name(key_code: int | None, characters: str | None, *, platform: str | None) -> str | None:
    """Return a stable key name, or ``None`` if no executable name can be derived."""
    table = _TABLES.get((platform or "windows").lower(), _WINDOWS_SPECIAL)
    if isinstance(key_code, int) and key_code in table:
        return table[key_code]
    text = characters or ""
    if text in _CONTROL_CHARACTER_NAMES:
        return _CONTROL_CHARACTER_NAMES[text]
    if len(text) == 1 and text.isprintable() and not text.isspace():
        return text.lower() if text.isalpha() else text
    if len(text) == 1 and ord(text) < 32:
        # Control characters such as Ctrl+Z ("\x1a") encode the letter as code + 96.
        return chr(ord(text) + 96)
    return None


def decode_modifiers(mask: int | None) -> list[str]:
    value = int(mask or 0)
    return [name for name in MODIFIER_NAMES if value & MODIFIER_BITS[name]]


def encode_modifiers(names: list[str]) -> int:
    return sum(MODIFIER_BITS[name] for name in names)
