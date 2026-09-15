from __future__ import annotations

import json
from pathlib import Path

from cad1000.prepare import compact_actions, load_event_index, validate_records, workflow_split


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_event_filtering_and_coordinate_normalization(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    write_jsonl(
        events,
        [
            {
                "type": "screen_config",
                "screens": [{"width": 1000, "height": 500}],
                "timestamp": 1000,
            },
            {"type": "mouse_move", "x": 10, "y": 10, "timestamp": 1100},
            {
                "type": "click",
                "x": 500,
                "y": 250,
                "isDown": True,
                "appName": "SolidWorks",
                "timestamp": 2000,
            },
            {
                "type": "click",
                "x": 500,
                "y": 250,
                "isDown": False,
                "appName": "SolidWorks",
                "timestamp": 2050,
            },
            {
                "type": "click",
                "x": 10,
                "y": 10,
                "isDown": True,
                "appName": "WhatsApp",
                "timestamp": 2100,
            },
            {"type": "privacy_mask_start", "timestamp": 2200},
            {
                "type": "key",
                "characters": "secret",
                "isDown": True,
                "appName": "SolidWorks",
                "timestamp": 2300,
            },
            {"type": "privacy_mask_end", "timestamp": 2400},
        ],
    )
    index = load_event_index(events, software="solidworks", application="SOLIDWORKS")
    assert index.raw_event_count == 8
    assert len(index.actions) == 1
    assert index.actions[0]["x"] == 0.5
    assert index.actions[0]["y"] == 0.5
    assert index.overlaps_privacy(1.25, 1.35)


def test_compact_actions_coalesces_typed_text() -> None:
    actions = [
        {"type": "key", "t": 2.0, "text": "2", "key_code": 50, "modifiers": 0},
        {"type": "key", "t": 2.1, "text": "5", "key_code": 53, "modifiers": 0},
        {"type": "key", "t": 2.2, "text": "\r", "key_code": 13, "modifiers": 0},
    ]
    compacted = compact_actions(actions, 1.0)
    assert compacted[0] == {"type": "type_text", "text": "25", "dt": 1.0}
    assert compacted[1]["type"] == "key"


def test_workflow_split_is_deterministic() -> None:
    value = workflow_split("solidworks/example", 0.9, 0.05)
    assert value == workflow_split("solidworks/example", 0.9, 0.05)
    assert value in {"train", "validation", "test"}


def test_validation_detects_valid_record(tmp_path: Path) -> None:
    path = tmp_path / "train.jsonl"
    write_jsonl(
        path,
        [
            {
                "id": "one",
                "split": "train",
                "source": {"software": "solidworks", "workflow_id": "abc"},
                "actions": [{"type": "click", "x": 0.2, "y": 0.8}],
            }
        ],
    )
    report = validate_records([path])
    assert report["record_count"] == 1
    assert report["errors"] == 0


def test_drag_reconstruction_from_press_drag_release(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    base = {"appName": "SolidWorks", "button": "left"}
    write_jsonl(
        events,
        [
            {"type": "screen_config", "screens": [{"width": 1000, "height": 1000}], "timestamp": 0},
            {"type": "click", "x": 100, "y": 100, "isDown": True, "timestamp": 1000, **base},
            {"type": "drag", "x": 150, "y": 150, "timestamp": 1050, **base},
            {"type": "drag", "x": 200, "y": 300, "timestamp": 1100, **base},
            {"type": "click", "x": 210, "y": 310, "isDown": False, "timestamp": 1200, **base},
            # Tiny jitter stays a click.
            {"type": "click", "x": 500, "y": 500, "isDown": True, "timestamp": 2000, **base},
            {"type": "drag", "x": 501, "y": 501, "timestamp": 2050, **base},
            {"type": "click", "x": 501, "y": 502, "isDown": False, "timestamp": 2100, **base},
            # Orphan drag samples never become actions.
            {"type": "drag", "x": 900, "y": 900, "timestamp": 3000, **base},
        ],
    )
    index = load_event_index(events, software="solidworks", application="SOLIDWORKS")
    assert [a["type"] for a in index.actions] == ["drag", "click"]
    drag = index.actions[0]
    assert (drag["x"], drag["y"], drag["end_x"], drag["end_y"]) == (0.1, 0.1, 0.21, 0.31)
    assert drag["path_points"] == 2 and drag["duration"] == 0.2
    assert index.drag_stats == {"drags": 1, "tiny_drags_as_click": 1, "orphan_drag_samples": 1}


def test_modified_printable_keys_are_not_typed_text() -> None:
    actions = [
        {"type": "key", "t": 2.0, "text": "a", "key_code": 65, "modifiers": 4},
        {"type": "key", "t": 2.1, "text": "b", "key_code": 66, "modifiers": 0},
    ]
    compacted = compact_actions(actions, 1.0)
    assert compacted[0]["type"] == "key" and compacted[1] == {"type": "type_text", "text": "b", "dt": 1.1}


def test_typed_value_is_not_split_by_span_boundary(tmp_path: Path) -> None:
    from cad1000.prepare import merge_split_typing

    events = tmp_path / "events.json"
    base = {"appName": "SolidWorks", "isDown": True, "modifiers": 0}
    write_jsonl(
        events,
        [
            {"type": "screen_config", "screens": [{"width": 1000, "height": 1000}], "timestamp": 0},
            {"type": "click", "x": 10, "y": 10, "button": "left", "timestamp": 1000, **base},
            {"type": "key", "keyCode": 52, "characters": "4", "timestamp": 1900, **base},
            {"type": "key", "keyCode": 48, "characters": "0", "timestamp": 2100, **base},
            {"type": "key", "keyCode": 13, "characters": "\r", "timestamp": 2300, **base},
        ],
    )
    index = load_event_index(events, software="solidworks", application="SOLIDWORKS")
    assert index.typing_groups == [(1, 2)]
    # A narration boundary at 2.0 s falls between "4" and "0": the whole value stays in span 1.
    first = index.actions_between(0.0, 2.0)
    second = index.actions_between(2.0, 3.0)
    assert [a.get("text") for a in first] == [None, "4", "0"]
    assert [a.get("text") for a in second] == ["\r"]
    assert compact_actions(first, 0.5)[1] == {"type": "type_text", "text": "40", "dt": 1.4}

    # Post-pass for shards prepared before this fix.
    rows = [
        {"source": {"software": "s", "workflow_id": "w"}, "task": {"application": "a", "task": "t", "description": "d", "requirements": []}, "observation": {"timestamp_s": 1.4}, "actions": [{"type": "click", "x": 0.1, "y": 0.1, "dt": 0.5}, {"type": "type_text", "text": "4", "dt": 0.5}]},
        {"source": {"software": "s", "workflow_id": "w"}, "task": {"application": "a", "task": "t", "description": "d", "requirements": []}, "observation": {"timestamp_s": 1.6}, "actions": [{"type": "type_text", "text": "0", "dt": 0.5}]},
        {"source": {"software": "s", "workflow_id": "w"}, "task": {"application": "a", "task": "t", "description": "d", "requirements": []}, "observation": {"timestamp_s": 9.0}, "actions": [{"type": "type_text", "text": "16", "dt": 0.5}]},
    ]
    merged, count = merge_split_typing(rows)
    assert count == 1 and len(merged) == 2
    assert merged[0]["actions"][-1]["text"] == "40" and merged[1]["actions"][0]["text"] == "16"
