from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad1000.policy_view import (
    GRID_MAX,
    PolicyConversionError,
    app_balanced_weights,
    build_prompt_messages,
    chunk_actions,
    convert_action,
    convert_file,
    convert_record,
    grid_to_pixels,
    parse_completion,
    serialize_target,
    to_grid,
    validate_policy_actions,
    validate_policy_records,
)


def neutral_row(**overrides):
    row = {
        "schema_version": "cad-vla-1.1",
        "id": "solidworks--wf1--00003",
        "split": "train",
        "source": {"software": "solidworks", "workflow_id": "wf1", "segment_index": 3, "platform": "windows"},
        "task": {
            "application": "SOLIDWORKS",
            "task": "Make a bracket",
            "description": "A bracket.",
            "requirements": ["R1", "R2"],
        },
        "observation": {"screen": {"width": 1920, "height": 1080}},
        "intent": "Open the sketch tool.",
        "action_summary": "Clicked the sketch tool and typed 25.",
        "details": "Nothing else.",
        "actions": [
            {"type": "click", "x": 0.5, "y": 0.25, "button": "left", "dt": 0.5},
            {"type": "type_text", "text": "25", "dt": 0.9},
            {"type": "key", "key_code": 13, "text": "\r", "modifiers": 0, "dt": 3.0},
        ],
        "images": ["frames/train/solidworks--wf1--00003.jpg"],
    }
    row.update(overrides)
    return row


def test_grid_round_trip_is_within_one_pixel() -> None:
    for width in (1280, 1920, 2560, 3840):
        for pixel in (0, 1, 17, width // 3, width - 1):
            grid = to_grid(pixel / width)
            assert 0 <= grid <= GRID_MAX
            assert abs(grid_to_pixels(grid, width) - pixel) <= width / GRID_MAX / 2 + 1e-6
    assert to_grid(0.0) == 0 and to_grid(1.0) == GRID_MAX
    with pytest.raises(PolicyConversionError):
        to_grid(1.2)


def test_action_conversion_to_policy_schema() -> None:
    assert convert_action({"type": "click", "x": 0.5, "y": 0.5}, platform="windows") == {
        "type": "click", "x": 500, "y": 500, "button": "left"
    }
    assert convert_action(
        {"type": "drag", "x": 0.1, "y": 0.1, "end_x": 0.2, "end_y": 0.3, "button": "left"},
        platform="windows",
    ) == {"type": "drag", "x": 100, "y": 100, "end_x": 200, "end_y": 300, "button": "left"}
    assert convert_action(
        {"type": "scroll", "x": 0.5, "y": 0.5, "delta_x": 0, "delta_y": -2.0}, platform="windows"
    ) == {"type": "scroll", "x": 500, "y": 500, "delta_x": 0, "delta_y": -2}
    assert convert_action(
        {"type": "key", "key_code": 90, "text": "\x1a", "modifiers": 2}, platform="windows"
    ) == {"type": "key", "key": "z", "modifiers": ["ctrl"]}
    assert convert_action({"type": "key", "key_code": 16, "text": "", "modifiers": 1}, platform="windows") is None
    with pytest.raises(PolicyConversionError):
        convert_action({"type": "drag", "x": 0.1, "y": 0.1}, platform="windows")  # legacy 1.0 drag
    assert convert_action({"type": "key", "key_code": 177, "text": "", "modifiers": 0}, platform="windows") is None  # media key
    with pytest.raises(PolicyConversionError):
        convert_action({"type": "key", "key_code": 9999, "text": "\u0001\u0002", "modifiers": 0}, platform="windows")


def test_chunking_respects_size_and_pauses() -> None:
    actions = [{"dt": 0.5}, {"dt": 0.9}, {"dt": 3.0}, {"dt": 3.1}]
    assert chunk_actions(actions, max_chunk=1, max_gap_s=1.0) == [{"dt": 0.5}]
    assert chunk_actions(actions, max_chunk=4, max_gap_s=1.0) == [{"dt": 0.5}, {"dt": 0.9}]
    assert len(chunk_actions(actions, max_chunk=4, max_gap_s=5.0)) == 4


def test_convert_record_single_action_and_no_leakage() -> None:
    record, reason = convert_record(neutral_row(), max_chunk=1)
    assert reason is None
    assert record["actions"] == [{"type": "click", "x": 500, "y": 250, "button": "left"}]
    assert record["completion"] == '{"actions":[{"type":"click","x":500,"y":250,"button":"left"}]}'
    assert record["span_action_count"] == 3
    prompt_dump = json.dumps(record["prompt"])
    assert "Clicked the sketch tool" not in prompt_dump
    assert "Nothing else" not in prompt_dump
    assert record["prompt"][1]["content"][0] == {"type": "image"}
    assert "Current intent: Open the sketch tool." in record["prompt"][1]["content"][1]["text"]
    assert build_prompt_messages(record) == record["prompt"]


def test_convert_record_chunk_and_drag_gate() -> None:
    record, _ = convert_record(neutral_row(), max_chunk=4, max_gap_s=1.0)
    assert [a["type"] for a in record["actions"]] == ["click", "type_text"]
    drag_row = neutral_row(actions=[{"type": "drag", "x": 0.1, "y": 0.1, "end_x": 0.5, "end_y": 0.5, "button": "left", "dt": 0.5}])
    assert convert_record(drag_row, drag_mode="exclude") == (None, "drag_excluded")
    included, _ = convert_record(drag_row, drag_mode="include")
    assert included["actions"][0]["type"] == "drag"
    assert convert_record(neutral_row(actions=[]))[1] == "no_actions"
    assert convert_record(neutral_row(images=[]))[1] == "no_image"


def test_strict_parse_and_schema_validation() -> None:
    actions = [{"type": "key", "key": "Enter", "modifiers": []}]
    parsed, error = parse_completion(serialize_target(actions))
    assert error is None and parsed == actions
    assert parse_completion("not json")[1].startswith("json")
    assert parse_completion('{"actions":[],"summary":"x"}')[1]
    assert parse_completion('```json\n{"actions":[]}\n```')[1]
    assert validate_policy_actions([{"type": "click", "x": 1000, "y": 0, "button": "left"}])
    assert validate_policy_actions([{"type": "click", "x": 1.5, "y": 0, "button": "left"}])
    assert validate_policy_actions([{"type": "click", "x": 1, "y": 0}])
    assert validate_policy_actions([{"type": "key", "key": "a", "modifiers": ["hyper"]}])
    assert validate_policy_actions([{"type": "type_text", "text": ""}])
    assert not validate_policy_actions([{"type": "scroll", "x": 1, "y": 2, "delta_x": 0, "delta_y": -3}])


def test_app_balanced_weights() -> None:
    softwares = ["autocad"] * 90 + ["catia"] * 10
    flat = app_balanced_weights(softwares, temperature=0.0)
    assert abs(sum(flat[:90]) - 0.5) < 1e-9 and abs(sum(flat[90:]) - 0.5) < 1e-9
    raw = app_balanced_weights(softwares, temperature=1.0)
    assert abs(sum(raw[:90]) - 0.9) < 1e-9


def test_convert_file_writes_relative_images_and_validates(tmp_path: Path) -> None:
    source_dir = tmp_path / "neutral"
    frame = source_dir / "frames" / "train" / "solidworks--wf1--00003.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpg")
    rows = [neutral_row(), neutral_row(id="solidworks--wf1--00004", images=["frames/train/missing.jpg"])]
    (source_dir / "all.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    output = tmp_path / "policy"
    report = convert_file(source_dir / "all.jsonl", output)
    assert report["example_count"] == 1
    assert report["rejected_reasons"] == {"missing_image_file": 1}
    record = json.loads((output / "train.jsonl").read_text().splitlines()[0])
    assert (output / record["image"]).resolve() == frame.resolve()
    assert validate_policy_records([output / "all.jsonl"])["record_count"] == 1


def test_policy_validation_detects_leakage_and_tampering(tmp_path: Path) -> None:
    record, _ = convert_record(neutral_row(), image_path="frame.jpg")
    (tmp_path / "frame.jpg").write_bytes(b"jpg")
    leaked = json.loads(json.dumps(record))
    leaked["id"] = "solidworks--wf1--00009"
    leaked["split"] = "test"
    path = tmp_path / "all.jsonl"
    path.write_text(json.dumps(record) + "\n" + json.dumps(leaked) + "\n")
    with pytest.raises(ValueError, match="leakage"):
        validate_policy_records([path])
    tampered = json.loads(json.dumps(record))
    tampered["completion"] = '{"actions": [{"type":"click","x":500,"y":250,"button":"left"}]}'
    path.write_text(json.dumps(tampered) + "\n")
    with pytest.raises(ValueError, match="canonical"):
        validate_policy_records([path])
