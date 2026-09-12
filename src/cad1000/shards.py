"""Restartable, storage-bounded per-workflow corpus preparation.

The full source corpus (~257 GiB) does not fit on the workspace disk, so workflows are processed
one at a time: download, validate sizes against the pinned Hugging Face tree, prepare examples
and frames into a durable shard, validate the shard, write an atomic manifest, and only then
remove that workflow's large recording files. Completed shards are skipped on restart.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import __version__
from .hf import DEFAULT_REPO, DEFAULT_REVISION, list_tree, list_workflows, sync_workflows
from .prepare import SCHEMA_VERSION, merge_split_typing, prepare_dataset, validate_records

MANIFEST_NAME = "manifest.json"
LARGE_FILES = ("clip.mp4", "events.json", "frame_events.json")
ALL_SOFTWARE = (
    "autocad",
    "catia",
    "nx-cad",
    "p-d5",
    "revit-architecture",
    "revit-structure",
    "sketchup",
    "solidworks",
    "staad-pro",
    "vray",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frames_digest(frames_root: Path) -> tuple[int, str]:
    """Combined digest over all frame files (sorted relative path, size, content)."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(frames_root.rglob("*.jpg")):
        count += 1
        digest.update(str(path.relative_to(frames_root)).encode("utf-8"))
        digest.update(str(path.stat().st_size).encode("utf-8"))
        digest.update(sha256_file(path).encode("utf-8"))
    return count, digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_manifest(shard_dir: Path) -> dict[str, Any] | None:
    path = shard_dir / MANIFEST_NAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else None


def free_gib(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 2**30


def _merge_source_manifest(raw_root: Path, records: list[dict[str, Any]]) -> None:
    path = raw_root / "source_manifest.json"
    existing: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        existing = loaded if isinstance(loaded, list) else []
    keyed = {f"{item.get('software')}/{item.get('workflow_id')}": item for item in existing}
    for record in records:
        keyed[f"{record['software']}/{record['workflow_id']}"] = record
    write_json_atomic(path, list(keyed.values()))


def plan_workflows(
    *,
    repo: str,
    revision: str,
    softwares: Iterable[str],
    workflow_ids: Iterable[str] | None,
    max_per_software: int | None,
) -> list[str]:
    """Round-robin workflow order across applications so partial runs stay balanced."""
    explicit = list(workflow_ids or [])
    if explicit:
        return [item if "/" in item else f"solidworks/{item}" for item in explicit]
    per_software: dict[str, list[str]] = {}
    for software in softwares:
        listed = list_workflows(repo, revision, software)
        per_software[software] = listed[:max_per_software] if max_per_software else listed
    ordered: list[str] = []
    depth = max((len(items) for items in per_software.values()), default=0)
    for index in range(depth):
        for items in per_software.values():
            if index < len(items):
                ordered.append(items[index])
    return ordered


def build_shards(
    raw_root: Path,
    shard_root: Path,
    *,
    repo: str = DEFAULT_REPO,
    revision: str = DEFAULT_REVISION,
    softwares: Iterable[str] = ALL_SOFTWARE,
    workflow_ids: Iterable[str] | None = None,
    max_per_software: int | None = None,
    max_workflows: int | None = None,
    min_free_gib: float = 40.0,
    cleanup: bool = True,
    include_inputs: bool = True,
    include_deliverables: bool = True,
    prepare_options: dict[str, Any] | None = None,
    log: Any = None,
) -> dict[str, Any]:
    log = log or (lambda message: print(message, file=sys.stderr, flush=True))
    prepare_options = dict(prepare_options or {})
    planned = plan_workflows(
        repo=repo,
        revision=revision,
        softwares=softwares,
        workflow_ids=workflow_ids,
        max_per_software=max_per_software,
    )
    summary: dict[str, Any] = {
        "planned": len(planned),
        "completed": 0,
        "skipped_existing": 0,
        "skipped_source": 0,
        "failed": 0,
        "stopped_reason": None,
        "shards": [],
    }
    processed = 0
    for workflow_path in planned:
        if max_workflows is not None and processed >= max_workflows:
            summary["stopped_reason"] = "max_workflows"
            break
        software, workflow_id = workflow_path.split("/", 1)
        shard_dir = shard_root / software / workflow_id
        manifest = read_manifest(shard_dir)
        if manifest and manifest.get("status") == "complete":
            summary["skipped_existing"] += 1
            continue
        if manifest and manifest.get("status") == "skipped":
            summary["skipped_source"] += 1
            continue
        processed += 1

        entries = list_tree(repo, revision, workflow_path, recursive=True)
        files = {entry["path"]: entry for entry in entries if entry.get("type") == "file"}
        required = [f"{workflow_path}/{name}" for name in ("clip.mp4", "events.json", "narration.json", "task_desc.json", "rubrics.json", "metadata.json")]
        missing = [path for path in required if path not in files]
        if missing:
            write_json_atomic(
                shard_dir / MANIFEST_NAME,
                _base_manifest(repo, revision, software, workflow_id)
                | {"status": "skipped", "reason": f"missing source files: {missing}"},
            )
            summary["skipped_source"] += 1
            log(f"[skip] {workflow_path}: missing {missing}")
            continue
        needed_gib = sum(int(files[path].get("size", 0)) for path in files) / 2**30
        available = free_gib(raw_root)
        if available - needed_gib < min_free_gib:
            summary["stopped_reason"] = (
                f"insufficient disk: {available:.1f} GiB free, workflow needs {needed_gib:.2f} GiB, "
                f"floor {min_free_gib} GiB"
            )
            log(f"[stop] {summary['stopped_reason']}")
            break

        started = time.time()
        log(f"[sync] {workflow_path} ({needed_gib:.2f} GiB)")
        try:
            records = sync_workflows(
                raw_root,
                repo=repo,
                revision=revision,
                software=software,
                workflow_ids=[workflow_path],
                max_workflows=1,
                include_video=True,
                include_frame_events=False,
                include_inputs=include_inputs,
                include_deliverables=include_deliverables,
            )
            _merge_source_manifest(raw_root, records)
            source_files = records[0]["files"]
            _verify_sizes(raw_root / workflow_path, source_files)

            if shard_dir.exists():
                for stale in ("all.jsonl", "train.jsonl", "validation.jsonl", "test.jsonl", "report.json"):
                    (shard_dir / stale).unlink(missing_ok=True)
                shutil.rmtree(shard_dir / "frames", ignore_errors=True)
            log(f"[prepare] {workflow_path}")
            report = prepare_dataset(
                raw_root,
                shard_dir,
                extract_frames=True,
                workflows=[raw_root / workflow_path],
                **prepare_options,
            )
            validation = validate_records([shard_dir / "all.jsonl"])
            frame_count, frame_hash = frames_digest(shard_dir / "frames")
            if frame_count != report["example_count"]:
                raise RuntimeError(
                    f"frame count {frame_count} != example count {report['example_count']}"
                )
            manifest = _base_manifest(repo, revision, software, workflow_id) | {
                "status": "complete",
                "source_files": source_files,
                "prepare_report": report,
                "validation": validation,
                "checksums": {
                    name: sha256_file(shard_dir / name)
                    for name in ("all.jsonl", "train.jsonl", "validation.jsonl", "test.jsonl")
                },
                "frames": {"count": frame_count, "sha256": frame_hash},
                "elapsed_s": round(time.time() - started, 1),
                "cleanup": {"removed": []},
            }
            write_json_atomic(shard_dir / MANIFEST_NAME, manifest)
            if cleanup:
                removed = cleanup_workflow(raw_root, software, workflow_id, shard_dir)
                manifest["cleanup"] = {"removed": removed}
                write_json_atomic(shard_dir / MANIFEST_NAME, manifest)
            summary["completed"] += 1
            summary["shards"].append(
                {"workflow": workflow_path, "examples": report["example_count"], "split": (report["split_counts"] and next(iter(report["split_counts"])))}
            )
            log(
                f"[done] {workflow_path}: {report['example_count']} examples, "
                f"{manifest['elapsed_s']}s"
            )
        except Exception as error:  # noqa: BLE001 - keep the loop restartable
            summary["failed"] += 1
            write_json_atomic(
                shard_dir / MANIFEST_NAME,
                _base_manifest(repo, revision, software, workflow_id)
                | {"status": "failed", "reason": f"{type(error).__name__}: {error}"},
            )
            log(f"[fail] {workflow_path}: {type(error).__name__}: {error}")
    return summary


def _base_manifest(repo: str, revision: str, software: str, workflow_id: str) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "repo": repo,
        "revision": revision,
        "software": software,
        "workflow_id": workflow_id,
        "created_unix": int(time.time()),
    }


def _verify_sizes(workflow_dir: Path, files: list[dict[str, Any]]) -> None:
    for item in files:
        expected = int(item.get("size") or 0)
        if not expected:
            continue
        matches = [path for path in workflow_dir.rglob(item["name"]) if path.is_file()]
        if not matches:
            raise FileNotFoundError(f"downloaded file missing: {item['name']}")
        if not any(path.stat().st_size == expected for path in matches):
            raise RuntimeError(
                f"size mismatch for {item['name']}: expected {expected}, "
                f"got {[path.stat().st_size for path in matches]}"
            )


def cleanup_workflow(raw_root: Path, software: str, workflow_id: str, shard_dir: Path) -> list[str]:
    """Delete only the validated workflow's large recording files."""
    manifest = read_manifest(shard_dir)
    if not manifest or manifest.get("status") != "complete":
        raise RuntimeError("refusing cleanup: shard manifest is not complete")
    if manifest.get("software") != software or manifest.get("workflow_id") != workflow_id:
        raise RuntimeError("refusing cleanup: manifest does not match workflow")
    workflow_dir = (raw_root / software / workflow_id).resolve()
    if workflow_dir.parent.parent != raw_root.resolve():
        raise RuntimeError(f"refusing cleanup outside raw root: {workflow_dir}")
    removed: list[str] = []
    for name in LARGE_FILES:
        target = workflow_dir / name
        if target.is_file():
            target.unlink()
            removed.append(name)
    return removed


def completed_shards(shard_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    for manifest_path in sorted(shard_root.glob("*/*/manifest.json")):
        manifest = read_manifest(manifest_path.parent)
        if manifest and manifest.get("status") == "complete":
            result.append((manifest_path.parent, manifest))
    return result


def merge_shards(shard_root: Path, output_dir: Path, *, verify: bool = True) -> dict[str, Any]:
    """Concatenate validated shards into one corpus directory with relative frame paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    shards = completed_shards(shard_root)
    if not shards:
        raise FileNotFoundError(f"No completed shards below {shard_root}")
    handles = {
        name: (output_dir / f"{name}.jsonl").open("w", encoding="utf-8")
        for name in ("all", "train", "validation", "test")
    }
    counts = {"all": 0, "train": 0, "validation": 0, "test": 0}
    typing_merges = 0
    included = []
    try:
        for shard_dir, manifest in shards:
            if verify:
                actual = sha256_file(shard_dir / "all.jsonl")
                if actual != manifest["checksums"]["all.jsonl"]:
                    raise RuntimeError(f"checksum mismatch for {shard_dir}/all.jsonl")
            with (shard_dir / "all.jsonl").open("r", encoding="utf-8") as source:
                shard_rows = [json.loads(line) for line in source if line.strip()]
            if manifest.get("prepare_report", {}).get("recipe", "").startswith("action-aligned-"):
                merged_here = 0  # Targets/frames are immutable pairs in the aligned recipe.
            else:
                shard_rows, merged_here = merge_split_typing(shard_rows)
            typing_merges += merged_here
            if True:
                for row in shard_rows:
                    if row.get("images"):
                        rewritten = [
                            os.path.relpath((shard_dir / image).resolve(), output_dir.resolve())
                            for image in row["images"]
                        ]
                        row["images"] = rewritten
                        row.setdefault("observation", {})["image"] = rewritten[0]
                    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    handles["all"].write(encoded)
                    handles[row["split"]].write(encoded)
                    counts["all"] += 1
                    counts[row["split"]] += 1
            included.append(
                {
                    "shard": str(shard_dir.relative_to(shard_root)),
                    "examples": manifest["prepare_report"]["example_count"],
                    "checksums": manifest["checksums"],
                    "frames": manifest["frames"],
                    "revision": manifest["revision"],
                }
            )
    finally:
        for handle in handles.values():
            handle.close()
    validation = validate_records([output_dir / "all.jsonl"])
    merged = {
        "manifest_version": 1,
        "schema_version": SCHEMA_VERSION,
        "shard_root": str(shard_root),
        "shard_count": len(included),
        "counts": counts,
        "typing_merges": typing_merges,
        "validation": validation,
        "shards": included,
        "checksums": {
            name: sha256_file(output_dir / f"{name}.jsonl")
            for name in ("all", "train", "validation", "test")
        },
    }
    write_json_atomic(output_dir / MANIFEST_NAME, merged)
    return merged
