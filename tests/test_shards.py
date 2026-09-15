from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad1000 import shards
from cad1000.shards import (
    build_shards,
    cleanup_workflow,
    frames_digest,
    merge_shards,
    plan_workflows,
    read_manifest,
    write_json_atomic,
)


def _fake_tree(_repo, _revision, path, recursive=False, **_):
    return [
        {"type": "file", "path": f"{path}/{name}", "size": 3, "oid": "abc"}
        for name in ("clip.mp4", "events.json", "narration.json", "task_desc.json", "rubrics.json", "metadata.json")
    ]


def _fake_sync(root: Path, *, workflow_ids, **_):
    workflow_path = workflow_ids[0]
    software, workflow_id = workflow_path.split("/", 1)
    directory = root / software / workflow_id
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("clip.mp4", "events.json", "narration.json", "task_desc.json", "rubrics.json", "metadata.json"):
        (directory / name).write_bytes(b"xyz")
    return [
        {
            "repo": "r",
            "revision": "v",
            "software": software,
            "workflow_id": workflow_id,
            "path": str(directory),
            "files": [{"name": "clip.mp4", "size": 3, "oid": "abc"}],
        }
    ]


def _fake_prepare(raw_root: Path, output_root: Path, *, workflows, extract_frames, **_):
    output_root.mkdir(parents=True, exist_ok=True)
    workflow = workflows[0]
    row = {
        "id": f"{workflow.parent.name}--{workflow.name}--00000",
        "split": "train",
        "source": {"software": workflow.parent.name, "workflow_id": workflow.name},
        "actions": [{"type": "click", "x": 0.5, "y": 0.5}],
        "images": [f"frames/train/{workflow.name}.jpg"],
        "observation": {},
    }
    frame = output_root / "frames" / "train" / f"{workflow.name}.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpg")
    for name in ("all", "train", "validation", "test"):
        rows = [row] if name in ("all", "train") else []
        (output_root / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {"example_count": 1, "split_counts": {"train": 1}}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(shards, "list_tree", _fake_tree)
    monkeypatch.setattr(shards, "sync_workflows", _fake_sync)
    monkeypatch.setattr(shards, "prepare_dataset", _fake_prepare)
    monkeypatch.setattr(shards, "free_gib", lambda _path: 1000.0)


def test_build_is_restartable_and_cleans_only_large_files(tmp_path: Path, patched) -> None:
    raw, out = tmp_path / "raw", tmp_path / "shards"
    summary = build_shards(raw, out, workflow_ids=["solidworks/wf1"], min_free_gib=1)
    assert summary["completed"] == 1 and summary["failed"] == 0
    manifest = read_manifest(out / "solidworks" / "wf1")
    assert manifest["status"] == "complete"
    assert manifest["frames"]["count"] == 1
    assert set(manifest["cleanup"]["removed"]) == {"clip.mp4", "events.json"}
    assert not (raw / "solidworks" / "wf1" / "clip.mp4").exists()
    assert (raw / "solidworks" / "wf1" / "narration.json").exists()
    assert json.loads((raw / "source_manifest.json").read_text())[0]["workflow_id"] == "wf1"

    again = build_shards(raw, out, workflow_ids=["solidworks/wf1"], min_free_gib=1)
    assert again["skipped_existing"] == 1 and again["completed"] == 0


def test_disk_floor_stops_before_download(tmp_path: Path, patched, monkeypatch) -> None:
    monkeypatch.setattr(shards, "free_gib", lambda _path: 10.0)
    summary = build_shards(tmp_path / "raw", tmp_path / "shards", workflow_ids=["solidworks/wf1"], min_free_gib=40)
    assert summary["completed"] == 0
    assert "insufficient disk" in summary["stopped_reason"]


def test_failed_prepare_records_failure_and_keeps_raw(tmp_path: Path, patched, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(shards, "prepare_dataset", boom)
    summary = build_shards(tmp_path / "raw", tmp_path / "shards", workflow_ids=["solidworks/wf1"])
    assert summary["failed"] == 1
    manifest = read_manifest(tmp_path / "shards" / "solidworks" / "wf1")
    assert manifest["status"] == "failed" and "ffmpeg exploded" in manifest["reason"]
    assert (tmp_path / "raw" / "solidworks" / "wf1" / "clip.mp4").exists()


def test_cleanup_refuses_incomplete_or_mismatched_manifest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "solidworks" / "wf1").mkdir(parents=True)
    shard = tmp_path / "shards" / "solidworks" / "wf1"
    with pytest.raises(RuntimeError, match="not complete"):
        cleanup_workflow(raw, "solidworks", "wf1", shard)
    write_json_atomic(shard / "manifest.json", {"status": "complete", "software": "solidworks", "workflow_id": "other"})
    with pytest.raises(RuntimeError, match="does not match"):
        cleanup_workflow(raw, "solidworks", "wf1", shard)


def test_merge_rewrites_relative_frame_paths(tmp_path: Path, patched) -> None:
    raw, out = tmp_path / "raw", tmp_path / "shards"
    build_shards(raw, out, workflow_ids=["solidworks/wf1", "catia/wf2"])
    merged_dir = tmp_path / "corpus"
    merged = merge_shards(out, merged_dir)
    assert merged["shard_count"] == 2 and merged["counts"]["all"] == 2
    rows = [json.loads(line) for line in (merged_dir / "all.jsonl").read_text().splitlines()]
    for row in rows:
        assert (merged_dir / row["images"][0]).resolve().exists()
        assert row["observation"]["image"] == row["images"][0]


def test_plan_workflows_round_robins(monkeypatch) -> None:
    listing = {"autocad": ["autocad/a1", "autocad/a2", "autocad/a3"], "catia": ["catia/c1"]}
    monkeypatch.setattr(shards, "list_workflows", lambda _r, _v, software: listing[software])
    assert plan_workflows(repo="r", revision="v", softwares=["autocad", "catia"], workflow_ids=None, max_per_software=None) == [
        "autocad/a1", "catia/c1", "autocad/a2", "autocad/a3"
    ]
    assert plan_workflows(repo="r", revision="v", softwares=["autocad"], workflow_ids=None, max_per_software=1) == ["autocad/a1"]


def test_frames_digest_changes_with_content(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"1")
    count, first = frames_digest(tmp_path)
    (tmp_path / "a.jpg").write_bytes(b"2")
    assert count == 1 and frames_digest(tmp_path)[1] != first
