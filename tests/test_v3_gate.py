"""Regression tests for fail-closed training-view parity and data-only launching."""
import importlib.util
import json
from pathlib import Path

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def trajectory(tmp_path, monkeypatch):
    audit = module("audit_v3")
    monkeypatch.setattr(audit, "validate_trajectory_records", lambda _: None)
    folder = tmp_path / "shard"
    (folder / "trajectory").mkdir(parents=True)
    action = {"type": "key", "key": "Enter", "modifiers": []}
    source = {"actions": [action], "completion": json.dumps({"actions": [action]}), "intent": "continue", "image": "frames/a.jpg"}
    row = {"id": "window", "split": "train", "steps": [source | {"id": "a"}], "images": ["../frames/a.jpg"]}
    line = json.dumps(row) + "\n"
    for split in ("all", "train", "validation", "test"):
        (folder / "trajectory" / f"{split}.jsonl").write_text(line if split in ("all", "train") else "")
    return audit, folder, {"a": source}, row


def test_trajectory_gate_accepts_exact_source(trajectory):
    audit, folder, sources, _ = trajectory
    assert audit.audit_trajectory(folder, sources, "train") == []


@pytest.mark.parametrize("field", ["image", "intent", "completion", "actions", "duplicate", "split_file"])
def test_trajectory_gate_rejects_changed_or_duplicated_labels(trajectory, field):
    audit, folder, sources, row = trajectory
    if field == "image":
        row["images"][0] = "../frames/wrong.jpg"
    elif field == "duplicate":
        row["steps"] *= 2
        row["images"] *= 2
    elif field == "split_file":
        (folder / "trajectory/train.jsonl").write_text("")
    else:
        row["steps"][0][field] = [] if field == "actions" else "wrong"
    (folder / "trajectory/all.jsonl").write_text(json.dumps(row) + "\n")
    assert audit.audit_trajectory(folder, sources, "train")


def test_full_build_commands_are_uncapped_and_data_only():
    runner = module("v3_data_only")
    for index in range(4):
        command = runner.worker_command(index, 4)
        assert command[1] == "scripts/rebuild_v3.py"
        assert "--max-actions" not in command and "--sample-per-app" not in command
        assert command[command.index("--worker-index") + 1] == str(index)
        assert "--cleanup" in command and str(runner.CACHE) in command


def test_trajectory_gate_rejects_fabricated_history(trajectory):
    audit, folder, sources, row = trajectory
    context = {"software": "app", "application": "CAD", "task": {}, "screen": {"width": 100, "height": 100}}
    sources["a"].update(context)
    row.update(context, first_step_number=1, total_steps=1, omitted_history_steps=0,
               history_lines=["0. invented history -> {}"])
    (folder / "trajectory/all.jsonl").write_text(json.dumps(row) + "\n")
    assert any("history differs" in e for e in audit.audit_trajectory(folder, sources, "train"))


def test_rebuild_gate_refuses_training_authorization(tmp_path, monkeypatch):
    runner = module("v3_data_only")
    (tmp_path / "hold.md").touch()
    (tmp_path / "training-hold.md").touch()
    monkeypatch.setattr(runner, "HOLD", tmp_path / "hold.md")
    monkeypatch.setattr(runner, "RUN", tmp_path)
    with pytest.raises(ValueError, match="does not authorize a data-only rebuild"):
        runner.check_gate({"status": "passed_for_rebuild", "training_authorized": True})


def test_missing_hold_refuses_rebuild(tmp_path, monkeypatch):
    runner = module("v3_data_only")
    monkeypatch.setattr(runner, "HOLD", tmp_path / "missing")
    with pytest.raises(ValueError, match="hold missing"):
        runner.check_gate({})
