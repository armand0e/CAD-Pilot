"""Incremental, immutable v3 rebuild with pinned downloads and explicit quality gates.

Use harness/.venv/bin/python (PyAV/Pillow). No training is launched by this builder.
"""
from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cad1000.aligned import RECIPE, prepare_aligned
from cad1000.hf import DEFAULT_REPO, DEFAULT_REVISION, download_file, list_tree
from cad1000.project_splits import project_split_plan
from cad1000.shards import completed_shards, frames_digest, sha256_file, write_json_atomic
from cad1000.prepare import validate_records
from cad1000.trajectory_view import convert_trajectories, validate_trajectory_records

FILES = ("events.json", "clip.mp4", "metadata.json", "task_desc.json", "rubrics.json", "narration.json")


def fingerprint():
    files = [Path(__file__), *Path("src/cad1000").glob("*.py")]
    return hashlib.sha256("".join(sha256_file(p) for p in sorted(files)).encode()).hexdigest()


def source_files(key, cache):
    entries = {e["path"]: e for e in list_tree(DEFAULT_REPO, DEFAULT_REVISION, key, recursive=True) if e.get("type") == "file"}
    folder = cache / key
    receipts = []
    for name in FILES:
        entry = entries[f"{key}/{name}"]
        target = folder / name
        if not target.exists():
            # Reuse retained recordings/metadata without moving or deleting them.
            existing = next((r / key / name for r in sorted(Path("data").glob("raw*"))
                             if r != cache and (r / key / name).is_file() and (r / key / name).stat().st_size == entry["size"]), None)
            target.parent.mkdir(parents=True, exist_ok=True)
            if existing:
                shutil.copyfile(existing, target)
            else:
                download_file(DEFAULT_REPO, DEFAULT_REVISION, f"{key}/{name}", target)
        if target.stat().st_size != entry["size"]:
            raise ValueError(f"source size mismatch: {key}/{name}")
        digest = sha256_file(target)
        lfs = entry.get("lfs") or {}
        if lfs.get("oid"):
            if digest != lfs["oid"]:
                raise ValueError(f"source SHA256 mismatch: {key}/{name}")
        else:
            blob = hashlib.sha1(f"blob {target.stat().st_size}\0".encode())
            with target.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    blob.update(block)
            if blob.hexdigest() != entry["oid"]:
                raise ValueError(f"source Git blob mismatch: {key}/{name}")
        receipts.append({"path": f"{key}/{name}", "sha256": digest, "oid": entry["oid"], "size": entry["size"]})
    return folder, receipts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/shards-v3"))
    parser.add_argument("--cache", type=Path, default=Path("data/raw-v3-cache"))
    parser.add_argument("--splits", type=Path, default=Path("data/splits-v3.json"))
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--sample-per-app", type=int, default=0, help="Train-only representative workflows; 0 processes the full plan")
    parser.add_argument("--workflow", action="append")
    parser.add_argument("--max-actions", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--min-free-gib", type=float, default=100)
    parser.add_argument("--cleanup", action="store_true", help="Delete ONLY cached source files after their new shard validates")
    args = parser.parse_args()
    manifests = [m for _, m in completed_shards(Path("data/shards"))]
    if len(manifests) != 597:
        raise SystemExit("Expected all 597 original shards before freezing project splits")
    plan = project_split_plan(manifests)
    if args.splits.exists():
        if json.loads(args.splits.read_text()) != plan:
            raise SystemExit("Frozen split plan differs; choose a new version/path")
    else:
        write_json_atomic(args.splits, plan)
    print(json.dumps({"splits": plan["per_app"], "limitations": plan["coverage_limitations"]}), flush=True)
    if args.plan_only:
        return
    assignments = plan["assignments"]
    keys = sorted(assignments)
    if args.workflow:
        keys = args.workflow
    elif args.sample_per_app:
        keys = []
        for app in plan["per_app"]:
            candidates = [k for k in assignments if k.startswith(app + "/") and assignments[k]["split"] == "train"]
            keys.extend(sorted(candidates, key=lambda k: hashlib.sha256(k.encode()).hexdigest())[:args.sample_per_app])
    if not 0 <= args.worker_index < args.workers:
        raise ValueError("Invalid worker partition")
    keys = keys[args.worker_index::args.workers]
    # Immutable manifest binds exact code, split plan, limits and decoder dependency.
    import av
    build = {"recipe": RECIPE, "code_sha256": fingerprint(), "split_sha256": sha256_file(args.splits),
             "max_actions": args.max_actions, "decoder": av.__version__}
    args.output.mkdir(parents=True, exist_ok=True)
    build_path = args.output / "build.json"
    with (args.output / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if build_path.exists() and json.loads(build_path.read_text()) != build:
            raise SystemExit("Code/config changed since this build began; use a new output directory")
        write_json_atomic(build_path, build)
    for key in keys:
        if key not in assignments:
            raise ValueError(f"Unknown workflow: {key}")
        destination = args.output / key
        manifest_path = destination / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("status") == "complete" and manifest.get("build") == build:
                if sha256_file(destination / "all.jsonl") != manifest["checksums"]["all.jsonl"]:
                    raise ValueError(f"completed shard changed: {key}")
                print(f"[skip] {key}", flush=True)
                continue
        free = shutil.disk_usage(args.output).free / 2**30
        if free < args.min_free_gib + 8:
            raise SystemExit(f"Disk floor reached ({free:.1f} GiB free); no files deleted")
        started = time.time()
        print(f"[source] {key}", flush=True)
        raw, receipts = source_files(key, args.cache)
        # Failed partial outputs are moved aside, not mixed with new frames or destructively removed.
        if destination.exists():
            archive = args.output / "incomplete" / f"{key.replace('/', '--')}--{time.time_ns()}"
            archive.parent.mkdir(exist_ok=True)
            destination.rename(archive)
        software, workflow = key.split("/", 1)
        provenance = {"dataset": DEFAULT_REPO, "revision": DEFAULT_REVISION, "software": software,
                      "workflow_id": workflow, "project_group": assignments[key]["project_group"]}
        print(f"[prepare] {key}", flush=True)
        report = prepare_aligned(raw, destination, split=assignments[key]["split"], provenance=provenance, max_actions=args.max_actions)
        validation = validate_records([destination / "all.jsonl"])
        count, digest = frames_digest(destination / "frames")
        if count != report["example_count"]:
            raise ValueError("frame/row count mismatch")
        trajectories = convert_trajectories(destination / "all.jsonl", destination / "trajectory", budget_tokens=57344, drag_mode="include")
        validate_trajectory_records([destination / "trajectory/all.jsonl"])
        if trajectories["supervised_steps"] != count:
            raise ValueError("trajectory conversion lost accepted actions")
        manifest = {"status": "complete", "build": build, "software": software, "workflow_id": workflow,
                    "revision": DEFAULT_REVISION, "prepare_report": report, "validation": validation,
                    "source_files": receipts, "frames": {"count": count, "sha256": digest},
                    "checksums": {f"{n}.jsonl": sha256_file(destination / f"{n}.jsonl") for n in ("all", "train", "validation", "test")},
                    "trajectory_report": trajectories, "elapsed_s": time.time() - started}
        write_json_atomic(manifest_path, manifest)
        print(f"[done] {key}: {count} actions; {report['episodes']} episodes; {time.time() - started:.1f}s", flush=True)
        if args.cleanup:
            if raw.resolve().parent.parent != args.cache.resolve():
                raise ValueError("cache cleanup path is outside the explicit cache root")
            for name in ("events.json", "clip.mp4"):
                (raw / name).unlink()
            print(f"[cleanup] {key}: removed cached events/video; pinned sources are downloadable", flush=True)


if __name__ == "__main__":
    main()
