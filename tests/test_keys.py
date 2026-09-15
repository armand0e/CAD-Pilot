from __future__ import annotations

from cad1000.keys import decode_modifiers, encode_modifiers, key_name


def test_windows_virtual_key_codes() -> None:
    assert key_name(13, "\r", platform="windows") == "Enter"
    assert key_name(27, "\x1b", platform="windows") == "Escape"
    assert key_name(100, "4", platform="windows") == "4"  # numpad
    assert key_name(90, "\x1a", platform="windows") == "z"  # Ctrl+Z
    assert key_name(46, "", platform="windows") == "Delete"
    assert key_name(113, "", platform="windows") == "F2"


def test_mac_and_fallbacks() -> None:
    assert key_name(36, "\r", platform="mac") == "Enter"
    assert key_name(0, "a", platform="mac") == "a"
    assert key_name(None, "\x1a", platform="linux") == "z"
    assert key_name(None, "Q", platform=None) == "q"
    assert key_name(9999, "", platform="windows") is None


def test_modifier_round_trip() -> None:
    assert decode_modifiers(2) == ["ctrl"]
    assert decode_modifiers(3) == ["ctrl", "shift"]
    assert decode_modifiers(0) == []
    assert encode_modifiers(["ctrl", "alt"]) == 6
