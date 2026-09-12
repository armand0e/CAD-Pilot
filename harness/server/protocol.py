"""Bounded desktop/agent messages and same-origin browser controls."""
import json
from urllib.parse import urlsplit

MODIFIERS = {"ctrl", "shift", "alt", "meta"}
BUTTONS = {"left", "middle", "right"}


def same_origin(origin: str | None, host: str) -> bool:
    # Non-browser local clients may omit Origin. Browsers cannot spoof it.
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower() and not parsed.path and not parsed.query and not parsed.fragment
    except ValueError:
        return False


def decode_message(raw: str) -> dict:
    if len(raw) > 32768:
        raise ValueError("Message exceeds the 32 KiB limit")
    message = json.loads(raw)
    if not isinstance(message, dict) or not isinstance(message.get("t"), str):
        raise ValueError("Expected an object with a message type")
    return message


def bounded_integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def desktop_message(message: dict, width: int, height: int) -> dict:
    kind = message["t"]
    allowed = {"move", "click", "down", "up", "release", "scroll", "key", "text"}
    if kind not in allowed:
        raise ValueError("Unknown desktop input type")
    result = {"t": kind}
    if kind in {"move", "click", "down", "up", "scroll"}:
        result["x"] = bounded_integer(message.get("x"), "x", 0, width - 1)
        result["y"] = bounded_integer(message.get("y"), "y", 0, height - 1)
    if kind in {"click", "down", "up"}:
        button = message.get("button", "left")
        if not isinstance(button, str) or button not in BUTTONS:
            raise ValueError("Unknown mouse button")
        result["button"] = button
    if kind == "click":
        result["clicks"] = bounded_integer(message.get("clicks", 1), "clicks", 1, 3)
    if kind in {"click", "down", "key", "scroll"}:
        modifiers = message.get("modifiers", [])
        if not isinstance(modifiers, list) or len(modifiers) > 4 or any(not isinstance(m, str) or m not in MODIFIERS for m in modifiers):
            raise ValueError("Modifiers must be a list of ctrl, shift, alt, meta")
        result["modifiers"] = sorted(set(modifiers))
    if kind == "scroll":
        for name in ("delta_x", "delta_y"):
            result[name] = bounded_integer(message.get(name, 0), name, -10, 10)
    if kind in {"key", "text"}:
        name, limit = ("key", 64) if kind == "key" else ("text", 8192)
        value = message.get(name)
        if not isinstance(value, str) or not 0 < len(value) <= limit or "\x00" in value:
            raise ValueError(f"{name} must contain 1..{limit} characters, without NUL")
        result[name] = value
    return result
