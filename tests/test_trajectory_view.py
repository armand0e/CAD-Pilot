from __future__ import annotations

import json
from pathlib import Path

from cad1000.imagetokens import image_token_count, jpeg_size
from cad1000.trajectory_view import (
    build_trajectory_messages,
    build_windows,
    convert_trajectories,
    validate_trajectory_records,
)


def make_step(i: int) -> dict:
    completion = json.dumps({"actions": [{"type": "click", "x": i, "y": i, "button": "left"}]}, separators=(",", ":"))
    return {"id": f"s{i}", "segment_index": i, "intent": f"intent {i}", "actions": json.loads(completion)["actions"], "completion": completion, "source_actions": []}


def test_image_token_count_matches_processor_rule() -> None:
    assert image_token_count(1280, 720) == 880  # 1280x704 -> 40x22
    assert image_token_count(640, 480) == 300  # 20x15
    assert image_token_count(3840, 2160, max_pixels=1048576) < 1100


def test_windows_are_consecutive_and_history_precedes() -> None:
    steps = [make_step(i) for i in range(30)]
    windows = build_windows(steps, budget_tokens=4000, image_tokens=[880] * 30, history_fraction=0.2)
    assert windows[0]["start"] == 0 and windows[-1]["end"] == 30
    for previous, current in zip(windows, windows[1:], strict=False):
        assert current["start"] == previous["end"]
        assert len(current["history_lines"]) + current["omitted"] == current["start"]
        assert all(int(line.split(".", 1)[0]) <= current["start"] for line in current["history_lines"])
        assert current["estimated_tokens"] <= 4000
    assert all(w["end"] - w["start"] >= 1 for w in windows)
    assert len(build_windows(steps, budget_tokens=4000, image_tokens=[880] * 30, max_windows=2)) == 2


def test_messages_layout() -> None:
    record = {
        "task": {"application": "CATIA", "task": "Part", "description": "D", "requirements": ["R"]},
        "first_step_number": 3,
        "history_lines": ["1. a -> {}", "2. b -> {}"],
        "omitted_history_steps": 0,
        "steps": [make_step(3), make_step(4)],
    }
    messages = build_trajectory_messages(record)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user", "assistant"]
    first = messages[1]["content"][1]["text"]
    assert "Earlier steps:\n1. a -> {}\n2. b -> {}" in first and "Step 3" in first
    assert messages[3]["content"][1]["text"].startswith("Step 4")
    assert messages[4]["content"] == record["steps"][1]["completion"]
    assert messages[1]["content"][0] == {"type": "image"}


def test_convert_trajectories_end_to_end(tmp_path: Path) -> None:
    from PIL import Image  # test-only dependency; the converter itself reads headers directly

    source = tmp_path / "neutral"
    (source / "frames" / "train").mkdir(parents=True)
    rows = []
    for i in range(6):
        image = source / "frames" / "train" / f"wf--{i:05d}.jpg"
        Image.new("RGB", (640, 480)).save(image)
        rows.append(
            {
                "schema_version": "cad-vla-1.1",
                "id": f"catia--wf--{i:05d}",
                "split": "train",
                "source": {"software": "catia", "workflow_id": "wf", "segment_index": i, "platform": "windows"},
                "task": {"application": "CATIA", "task": "Part", "description": "D", "requirements": []},
                "observation": {"screen": {"width": 1920, "height": 1080}},
                "intent": f"intent {i}",
                "action_summary": "hidden",
                "actions": [{"type": "click", "x": 0.1 * i, "y": 0.2, "button": "left", "dt": 0.5}],
                "images": [f"frames/train/wf--{i:05d}.jpg"],
            }
        )
    (source / "all.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert jpeg_size(source / "frames" / "train" / "wf--00000.jpg") == (640, 480)
    output = tmp_path / "traj"
    report = convert_trajectories(source / "all.jsonl", output, budget_tokens=1500)
    assert report["supervised_steps"] == 6 and report["window_count"] >= 2
    assert validate_trajectory_records([output / "all.jsonl"])["record_count"] == report["window_count"]
    first = json.loads((output / "train.jsonl").read_text().splitlines()[1])
    assert first["first_step_number"] == len(first["history_lines"]) + first["omitted_history_steps"] + 1
    assert (output / first["images"][0]).resolve().exists()
    assert "hidden" not in json.dumps(first)
