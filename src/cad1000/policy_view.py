"""Policy training view: one screenshot -> a short executable action chunk.

The neutral ``cad-vla-1.1`` row keeps every action of a narrated span but only the screenshot
before the first one. This module derives the first-run policy targets described in the training
handoff: strict JSON, integer ``0..999`` coordinates, at most a few actions per example, and no
label leakage from the narration's post-hoc summary. The prompt builder lives here so the
converter, the trainer, and the offline evaluator share one definition.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .keys import MODIFIER_KEY_NAMES, MODIFIER_NAMES, decode_modifiers, key_name
from .prepare import iter_json_lines

POLICY_SCHEMA_VERSION = "cad-policy-1.1"
GRID_MAX = 999
BUTTONS = ("left", "right", "middle")
POLICY_ACTION_TYPES = ("click", "drag", "scroll", "type_text", "key")

SYSTEM_PROMPT = (
    "You are a CAD desktop GUI operator policy. You receive one screenshot of the full screen "
    "and the current intent. Reply with exactly one JSON object of the form "
    '{"actions":[...]} and nothing else: no prose, no markdown, no explanation. '
    f"Coordinates are integers on a 0..{GRID_MAX} grid over the full screen "
    "(x increases to the right, y increases downward). Return 1 to 4 actions. "
    "Each action MUST contain a type field and exactly the fields shown below. "
    "These are valid format examples, not targets to copy; locate the actual target in the screenshot:\n"
    '{"actions":[{"type":"click","x":123,"y":456,"button":"left","modifiers":[],"clicks":1}]}\n'
    '{"actions":[{"type":"drag","x":123,"y":456,"end_x":234,"end_y":567,"button":"left","modifiers":[]}]}\n'
    '{"actions":[{"type":"scroll","x":123,"y":456,"delta_x":0,"delta_y":-1,"modifiers":[]}]}\n'
    '{"actions":[{"type":"type_text","text":"25.4"}]}\n'
    '{"actions":[{"type":"key","key":"n","modifiers":["ctrl"]}]}\n'
    'Buttons: "left", "right", "middle". Modifiers: "ctrl", "shift", "alt", "meta"; '
    'use [] for no modifiers. Both x and y are required for every click, drag, or scroll. '
    "Pointer modifiers are held throughout the gesture. Clicks is 1, 2, or 3. "
    "Scroll deltas are wheel ticks: positive y scrolls up, positive x right; never return both zero. "
    "Do not return a bare array or use action names as object keys."
)


def action_response_format(max_actions: int = 4) -> dict[str, Any]:
    """JSON Schema for constrained endpoint decoding; mirrors the executable action contract."""
    coordinate = {"type": "integer", "minimum": 0, "maximum": GRID_MAX}
    properties = {
        "x": coordinate, "y": coordinate, "end_x": coordinate, "end_y": coordinate,
        "button": {"type": "string", "enum": list(BUTTONS)},
        "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
        "delta_x": {"type": "integer", "minimum": -10, "maximum": 10}, "delta_y": {"type": "integer", "minimum": -10, "maximum": 10},
        "text": {"type": "string", "minLength": 1},
        "key": {"type": "string", "minLength": 1},
        "modifiers": {"type": "array", "items": {"type": "string", "enum": list(MODIFIER_NAMES)}},
    }
    fields = {"click": ["x", "y", "button", "modifiers", "clicks"], "drag": ["x", "y", "end_x", "end_y", "button", "modifiers"],
              "scroll": ["x", "y", "delta_x", "delta_y", "modifiers"], "type_text": ["text"], "key": ["key", "modifiers"]}
    variants = [{"type": "object", "properties": {"type": {"type": "string", "enum": [kind]},
                  **{field: properties[field] for field in names}}, "required": ["type", *names],
                 "additionalProperties": False} for kind, names in fields.items()]
    return {"type": "json_schema", "json_schema": {"name": "cad_actions", "strict": True,
            "schema": {"type": "object", "properties": {"actions": {"type": "array", "items": {"anyOf": variants},
                        "minItems": 1, "maxItems": max_actions}}, "required": ["actions"], "additionalProperties": False}}}


class PolicyConversionError(ValueError):
    """Raised when a neutral action cannot be expressed in the policy schema."""


def to_grid(value: float) -> int:
    if not 0.0 <= value <= 1.0:
        raise PolicyConversionError(f"normalized coordinate {value} outside [0,1]")
    return min(GRID_MAX, max(0, round(value * GRID_MAX)))


def grid_to_pixels(value: int, extent: int) -> float:
    """Inverse of :func:`to_grid` for a screen axis of ``extent`` pixels."""
    return (value / GRID_MAX) * extent


def convert_action(action: dict[str, Any], *, platform: str | None) -> dict[str, Any] | None:
    """Convert one neutral action to the policy schema.

    Returns ``None`` for actions that carry no executable content (a bare modifier key press).
    Raises :class:`PolicyConversionError` for actions that should reject the whole example.
    """
    kind = action.get("type")
    try:
        result = _convert_action(action, kind, platform=platform)
        if result is not None and kind in {"click", "drag", "scroll"}:
            if "modifiers" in action:
                result["modifiers"] = decode_modifiers(action["modifiers"])
            if kind == "click" and "clicks" in action:
                result["clicks"] = action["clicks"]
        return result
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PolicyConversionError):
            raise
        raise PolicyConversionError(f"malformed {kind} action: {error!r}") from error


def _convert_action(action: dict[str, Any], kind: Any, *, platform: str | None) -> dict[str, Any] | None:
    if kind == "click":
        return {
            "type": "click",
            "x": to_grid(float(action["x"])),
            "y": to_grid(float(action["y"])),
            "button": _button(action.get("button")),
        }
    if kind == "drag":
        return {
            "type": "drag",
            "x": to_grid(float(action["x"])),
            "y": to_grid(float(action["y"])),
            "end_x": to_grid(float(action["end_x"])),
            "end_y": to_grid(float(action["end_y"])),
            "button": _button(action.get("button")),
        }
    if kind == "scroll":
        if not action.get("delta_x") and not action.get("delta_y"):
            raise PolicyConversionError("zero_scroll")
        return {
            "type": "scroll",
            "x": to_grid(float(action["x"])),
            "y": to_grid(float(action["y"])),
            "delta_x": _int_delta(action.get("delta_x")),
            "delta_y": _int_delta(action.get("delta_y")),
        }
    if kind == "type_text":
        text = str(action.get("text") or "")
        if not text:
            raise PolicyConversionError("empty type_text")
        return {"type": "type_text", "text": text}
    if kind == "key":
        name = key_name(action.get("key_code"), action.get("text"), platform=platform)
        if name is None and not (action.get("text") or ""):
            return None  # media/system keys (volume, mute, Fn) produce no input: not an action
        if name is None:
            raise PolicyConversionError(
                f"unmapped key code {action.get('key_code')!r} text {action.get('text')!r}"
            )
        if name in MODIFIER_KEY_NAMES:
            return None
        return {"type": "key", "key": name, "modifiers": decode_modifiers(action.get("modifiers"))}
    raise PolicyConversionError(f"unsupported action type {kind!r}")


def _button(value: Any) -> str:
    button = str(value or "left").lower()
    if button not in BUTTONS:
        raise PolicyConversionError(f"unsupported button {value!r}")
    return button


def _int_delta(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyConversionError(f"non-numeric scroll delta {value!r}")
    if not math.isfinite(value):
        raise PolicyConversionError("non-finite scroll delta")
    return (1 if value > 0 else -1) * max(1, round(abs(value))) if value else 0


def validate_policy_actions(actions: Any) -> list[str]:
    """Return schema errors for a parsed ``actions`` list (empty list means valid)."""
    errors: list[str] = []
    if not isinstance(actions, list) or not actions:
        return ["actions must be a non-empty list"]
    for index, action in enumerate(actions):
        prefix = f"actions[{index}]"
        if not isinstance(action, dict):
            errors.append(f"{prefix}: not an object")
            continue
        kind = action.get("type")
        if kind not in POLICY_ACTION_TYPES:
            errors.append(f"{prefix}: bad type {kind!r}")
            continue
        expected = {
            "click": {"type", "x", "y", "button"},
            "drag": {"type", "x", "y", "end_x", "end_y", "button"},
            "scroll": {"type", "x", "y", "delta_x", "delta_y"},
            "type_text": {"type", "text"},
            "key": {"type", "key", "modifiers"},
        }[kind]
        optional = {"modifiers", "clicks"} if kind == "click" else {"modifiers"} if kind in {"drag", "scroll"} else set()
        if not expected <= set(action) or set(action) - expected - optional:
            errors.append(f"{prefix}: keys {sorted(action)} != {sorted(expected)}")
            continue
        for coordinate in ("x", "y", "end_x", "end_y"):
            if coordinate in action:
                value = action[coordinate]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= GRID_MAX:
                    errors.append(f"{prefix}: {coordinate}={value!r} not in 0..{GRID_MAX}")
        if "button" in action and action["button"] not in BUTTONS:
            errors.append(f"{prefix}: bad button {action['button']!r}")
        if "clicks" in action and (type(action["clicks"]) is not int or not 1 <= action["clicks"] <= 3):
            errors.append(f"{prefix}: clicks must be 1..3")
        if "modifiers" in action and (not isinstance(action["modifiers"], list) or any(m not in MODIFIER_NAMES for m in action["modifiers"])):
            errors.append(f"{prefix}: invalid modifiers")
        if kind == "scroll" and action.get("delta_x") == action.get("delta_y") == 0:
            errors.append(f"{prefix}: zero scroll is not an action")
        for delta in ("delta_x", "delta_y"):
            if delta in action and (isinstance(action[delta], bool) or not isinstance(action[delta], int)):
                errors.append(f"{prefix}: {delta} must be an integer")
            elif delta in action and abs(action[delta]) > 10:
                errors.append(f"{prefix}: {delta} exceeds 10 wheel ticks")
        if kind == "type_text" and (not isinstance(action["text"], str) or not action["text"]):
            errors.append(f"{prefix}: text must be a non-empty string")
        if kind == "key":
            if not isinstance(action["key"], str) or not action["key"]:
                errors.append(f"{prefix}: key must be a non-empty string")
            modifiers = action["modifiers"]
            if not isinstance(modifiers, list) or any(m not in MODIFIER_NAMES for m in modifiers):
                errors.append(f"{prefix}: modifiers must be a list drawn from {MODIFIER_NAMES}")
    return errors


def serialize_target(actions: list[dict[str, Any]]) -> str:
    return json.dumps({"actions": actions}, ensure_ascii=False, separators=(",", ":"))


def parse_completion(text: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Strictly parse a model completion. Returns ``(actions, error)``."""
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as error:
        return None, f"json: {error.msg}"
    if not isinstance(value, dict) or set(value) != {"actions"}:
        return None, "top-level must be an object with only an 'actions' key"
    errors = validate_policy_actions(value["actions"])
    if errors:
        return None, "; ".join(errors)
    return value["actions"], None


def prompt_text(record: dict[str, Any], *, include_requirements: bool = True, include_description: bool = True) -> str:
    task = record["task"]
    lines = [f"Application: {task.get('application') or record.get('software')}"]
    if task.get("task"):
        lines.append(f"Task: {task['task']}")
    if include_description and task.get("description"):
        lines.append(f"Description: {task['description']}")
    if include_requirements and task.get("requirements"):
        lines.append("Requirements:")
        lines.extend(f"- {item}" for item in task["requirements"])
    lines.append(f"Current intent: {record.get('intent') or ''}".rstrip())
    lines.append("Return the next action(s) for the current screenshot as JSON.")
    return "\n".join(lines)


def build_prompt_messages(record: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    """Chat messages (system + user with one image) used for training and inference."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt_text(record, **kwargs)},
            ],
        },
    ]


def chunk_actions(
    actions: list[dict[str, Any]], *, max_chunk: int, max_gap_s: float
) -> list[dict[str, Any]]:
    """Take the first ``max_chunk`` actions, stopping at the first long pause."""
    if max_chunk < 1:
        raise ValueError("max_chunk must be >= 1")
    chunk: list[dict[str, Any]] = []
    for action in actions:
        if chunk and action.get("dt", 0.0) - chunk[-1].get("dt", 0.0) > max_gap_s:
            break
        chunk.append(action)
        if len(chunk) >= max_chunk:
            break
    return chunk


def convert_record(
    row: dict[str, Any],
    *,
    max_chunk: int = 1,
    max_gap_s: float = 1.0,
    drag_mode: str = "exclude",
    image_path: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Convert one neutral row. Returns ``(policy_row, rejection_reason)``."""
    source = row.get("source") or {}
    platform = source.get("platform")
    if not row.get("actions"):
        return None, "no_actions"
    if image_path is None and not row.get("images"):
        return None, "no_image"
    converted: list[dict[str, Any]] = []
    neutral: list[dict[str, Any]] = []
    try:
        for action in row["actions"]:
            policy_action = convert_action(action, platform=platform)
            if policy_action is None:
                continue
            converted.append(policy_action)
            neutral.append(action)
    except PolicyConversionError as error:
        return None, f"conversion_error:{error}"
    if not converted:
        return None, "only_modifier_keys"
    chunk_source = chunk_actions(neutral, max_chunk=max_chunk, max_gap_s=max_gap_s)
    chunk = converted[: len(chunk_source)]
    if any(action["type"] == "drag" for action in chunk):
        if drag_mode == "exclude":
            return None, "drag_excluded"
        if drag_mode != "include":
            raise ValueError(f"unknown drag_mode {drag_mode!r}")
    errors = validate_policy_actions(chunk)
    if errors:
        return None, "invalid_target:" + "; ".join(errors)
    screen = (row.get("observation") or {}).get("screen") or {}
    record = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "id": row["id"],
        "split": row["split"],
        "source": {
            "software": source.get("software"),
            "workflow_id": source.get("workflow_id"),
            "segment_index": source.get("segment_index"),
            "platform": platform,
            "revision": source.get("revision"),
        },
        "software": source.get("software"),
        "application": (row.get("task") or {}).get("application"),
        "screen": {"width": screen.get("width"), "height": screen.get("height")},
        "image": image_path if image_path is not None else row["images"][0],
        "task": {
            "application": (row.get("task") or {}).get("application"),
            "task": (row.get("task") or {}).get("task"),
            "description": (row.get("task") or {}).get("description"),
            "requirements": list((row.get("task") or {}).get("requirements") or []),
        },
        "intent": row.get("intent"),
        "actions": chunk,
        "completion": serialize_target(chunk),
        "source_actions": chunk_source,
        "span_action_count": len(row["actions"]),
        "audit": {"action_summary": row.get("action_summary"), "details": row.get("details")},
    }
    record["prompt"] = build_prompt_messages(record)
    return record, None


def convert_file(
    input_path: Path,
    output_dir: Path,
    *,
    max_chunk: int = 1,
    max_gap_s: float = 1.0,
    drag_mode: str = "exclude",
) -> dict[str, Any]:
    """Convert a neutral JSONL file (plus its frames) into a policy-view directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_root = input_path.parent
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for row in iter_json_lines(input_path):
        image_path = None
        if row.get("images"):
            absolute = (frames_root / row["images"][0]).resolve()
            if not absolute.exists():
                rejected["missing_image_file"] += 1
                continue
            image_path = os.path.relpath(absolute, output_dir.resolve())
        record, reason = convert_record(
            row, max_chunk=max_chunk, max_gap_s=max_gap_s, drag_mode=drag_mode, image_path=image_path
        )
        if record is None:
            rejected[reason.split(":", 1)[0] if reason else "unknown"] += 1
            continue
        rows.append(record)
    report = write_policy_rows(rows, output_dir)
    report["rejected_reasons"] = dict(rejected.most_common())
    report["parameters"] = {
        "max_chunk": max_chunk,
        "max_gap_s": max_gap_s,
        "drag_mode": drag_mode,
        "input": str(input_path),
    }
    with (output_dir / "report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def write_policy_rows(rows: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    for name in ("all", "train", "validation", "test"):
        selected = rows if name == "all" else [row for row in rows if row["split"] == name]
        with (output_dir / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in selected:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return policy_statistics(rows)


def policy_statistics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    by_software: Counter[str] = Counter(str(row["software"]) for row in rows)
    by_split: Counter[str] = Counter(str(row["split"]) for row in rows)
    first_type: Counter[str] = Counter(row["actions"][0]["type"] for row in rows)
    all_types: Counter[str] = Counter(a["type"] for row in rows for a in row["actions"])
    chunk_lengths: Counter[int] = Counter(len(row["actions"]) for row in rows)
    workflows = {f"{row['software']}/{row['source']['workflow_id']}" for row in rows}
    completion_chars = sorted(len(row["completion"]) for row in rows)
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "example_count": len(rows),
        "workflow_count": len(workflows),
        "split_counts": dict(by_split),
        "software_counts": dict(by_software.most_common()),
        "first_action_types": dict(first_type.most_common()),
        "action_types": dict(all_types.most_common()),
        "chunk_length_counts": {str(k): v for k, v in sorted(chunk_lengths.items())},
        "completion_chars": {
            "p50": _percentile(completion_chars, 0.5),
            "p95": _percentile(completion_chars, 0.95),
            "max": completion_chars[-1] if completion_chars else 0,
        },
        "modifier_bits_note": "ctrl=2 verified from recordings; shift=1, alt=4, meta=8 inferred",
    }


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, round(fraction * (len(values) - 1)))
    return values[index]


def app_balanced_weights(softwares: list[str], *, temperature: float = 0.5) -> list[float]:
    """Per-example sampling weights that flatten application imbalance.

    With ``temperature=1`` sampling is proportional to raw counts; ``0`` makes every application
    equally likely; values in between are the usual temperature-based compromise.
    """
    counts = Counter(softwares)
    scaled = {name: count**temperature for name, count in counts.items()}
    total = sum(scaled.values())
    return [scaled[name] / total / counts[name] for name in softwares]


def iter_policy_rows(path: Path) -> Iterator[dict[str, Any]]:
    yield from iter_json_lines(path)


def validate_policy_records(paths: Iterable[Path]) -> dict[str, Any]:
    count = 0
    workflows_by_split: dict[str, set[str]] = {n: set() for n in ("train", "validation", "test")}
    errors: list[str] = []
    for path in paths:
        for row in iter_policy_rows(path):
            count += 1
            row_id = str(row.get("id", f"{path}:{count}"))
            split = row.get("split")
            if split not in workflows_by_split:
                errors.append(f"{row_id}: invalid split {split!r}")
                continue
            workflows_by_split[split].add(f"{row.get('software')}/{row['source'].get('workflow_id')}")
            parsed, error = parse_completion(row.get("completion", ""))
            if error:
                errors.append(f"{row_id}: {error}")
                continue
            if parsed != row.get("actions"):
                errors.append(f"{row_id}: completion does not round-trip to actions")
            if row.get("completion") != serialize_target(row["actions"]):
                errors.append(f"{row_id}: completion is not canonical JSON")
            image = row.get("image")
            if not image or not (path.parent / image).exists():
                errors.append(f"{row_id}: missing image {image!r}")
            user_text = json.dumps(row.get("prompt", []))
            summary = (row.get("audit") or {}).get("action_summary")
            if summary and summary in user_text:
                errors.append(f"{row_id}: action_summary leaked into prompt")
    leakage: set[str] = set()
    names = list(workflows_by_split)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            leakage.update(workflows_by_split[left] & workflows_by_split[right])
    if leakage:
        errors.append(f"workflow leakage across splits: {sorted(leakage)}")
    if errors:
        raise ValueError("Policy validation failed:\n" + "\n".join(errors[:50]))
    return {
        "record_count": count,
        "workflow_counts": {name: len(items) for name, items in workflows_by_split.items()},
        "errors": 0,
    }
