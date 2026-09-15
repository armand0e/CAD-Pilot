"""Streaming corpus audit. Reads completed shards; never rewrites training data.

Run with harness/.venv/bin/python scripts/audit_corpus_quality.py --output runs/corpus-audit
Image headers are checked exhaustively; full decoding uses a deterministic train-only sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cad1000.hf import DEFAULT_REVISION
from cad1000.policy_view import convert_record, parse_completion
from cad1000.prepare import workflow_split, TYPING_GAP_S


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    counts, issues, samples = Counter(), Counter(), defaultdict(list)
    apps, splits, targets, rejections, quality, modifiers, scroll, dimensions = (Counter() for _ in range(8))
    workflow_splits, clip_groups, task_groups, deliverable_groups = (defaultdict(set) for _ in range(4))
    per_app = defaultdict(Counter)
    review_images = defaultdict(list)
    all_ids = set()
    started = time.time()
    manifests = sorted(Path("data/shards").glob("*/*/manifest.json"))
    raw_locations = {}
    for narration in Path("data").glob("raw-*/*/*/narration.json"):
        raw_locations.setdefault((narration.parent.parent.name, narration.parent.name), narration.parent)

    def issue(name, row_id, detail=None):
        issues[name] += 1
        if len(samples[name]) < 8:
            samples[name].append({"id": row_id, "detail": detail})

    for ordinal, path in enumerate(manifests):
        manifest = json.loads(path.read_text())
        if manifest.get("status") != "complete":
            counts[f"shard_{manifest.get('status')}"] += 1
            continue
        counts["completed_shards"] += 1
        directory = path.parent
        software, workflow_id = manifest["software"], manifest["workflow_id"]
        key = f"{software}/{workflow_id}"
        if manifest.get("revision") != DEFAULT_REVISION:
            issue("wrong_source_revision", key, manifest.get("revision"))
        params = manifest["prepare_report"].get("parameters", {})
        for reason, n in manifest["prepare_report"].get("rejected_reasons", {}).items():
            rejections[f"prepare:{reason}"] += n
        for raw in manifest.get("source_files", []):
            if raw["name"] == "clip.mp4":
                clip_groups[raw["oid"]].add(key)
            elif raw["name"].lower().endswith((".dwg", ".sldprt", ".sldasm", ".rvt", ".catpart", ".prt", ".skp")):
                deliverable_groups[raw["oid"]].add(key)
        raw_dir = raw_locations.get((software, workflow_id))
        annotations = []
        if raw_dir:
            annotations = json.loads((raw_dir / "narration.json").read_text()).get("annotations", [])
            counts["shards_with_narration"] += 1
        rows = []
        digest = hashlib.sha256()
        with (directory / "all.jsonl").open("rb") as handle:
            for line in handle:
                digest.update(line)
                row = json.loads(line)
                rows.append(row)
        if digest.hexdigest() != manifest["checksums"]["all.jsonl"]:
            issue("checksum_mismatch", key, "all.jsonl")
        for split in ("train", "validation", "test"):
            digest_split = hashlib.sha256()
            with (directory / f"{split}.jsonl").open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest_split.update(block)
            if digest_split.hexdigest() != manifest["checksums"][f"{split}.jsonl"]:
                issue("checksum_mismatch", key, split)
        if len(rows) != manifest["prepare_report"]["example_count"]:
            issue("manifest_row_count", key, len(rows))
        local_frames = 0
        previous = None
        seen_frames = {}
        for row in rows:
            rid, source, observation = row["id"], row["source"], row["observation"]
            counts["rows"] += 1
            apps[software] += 1
            splits[row["split"]] += 1
            per_app[software][f"rows:{row['split']}"] += 1
            workflow_splits[key].add(row["split"])
            task_groups[" ".join(str(row["task"].get("task") or "").lower().split())].add(key)
            if rid in all_ids:
                issue("duplicate_id", rid)
            all_ids.add(rid)
            expected = workflow_split(key, params.get("train_ratio", .9), params.get("validation_ratio", .05))
            if row["split"] != expected:
                issue("split_assignment", rid)
            if source.get("revision") != DEFAULT_REVISION:
                issue("row_revision", rid)
            if not (row.get("intent") or "").strip():
                issue("empty_intent", rid)
            if not row["task"].get("task"):
                issue("empty_task", rid)
            quality[str(row.get("quality", {}).get("score"))] += 1
            for flag in row.get("quality", {}).get("flags", []):
                counts[f"retained_quality_flag:{flag}"] += 1
            actions = row.get("actions", [])
            counts["neutral_actions"] += len(actions)
            if not actions:
                issue("empty_actions", rid)
                continue
            first_dt = float(actions[0]["dt"])
            if first_dt < 1 / float(source.get("fps") or 30):
                issue("observation_under_one_frame_before_action", rid, first_dt)
            if first_dt > 1:
                issue("first_target_over_one_second_after_frame", rid, first_dt)
            t = float(observation["timestamp_s"])
            duration = source.get("workflow_duration_ms")
            if duration and t > duration / 1000:
                issue("observation_after_video", rid, t)
            last_dt = -math.inf
            for action in actions:
                counts[f"neutral_type:{action['type']}"] += 1
                if action["dt"] < last_dt or action["dt"] < 0:
                    issue("invalid_action_time", rid, action)
                last_dt = action["dt"]
                for coordinate in ("x", "y", "end_x", "end_y"):
                    if coordinate in action and (not math.isfinite(action[coordinate]) or not 0 <= action[coordinate] <= 1):
                        issue("invalid_coordinate", rid, action)
                if action["type"] == "key":
                    modifiers[str(action.get("modifiers", 0))] += 1
                if action["type"] == "scroll":
                    scroll[f"{action.get('delta_x')},{action.get('delta_y')}"] += 1
                if action["type"] == "type_text" and len(action["text"]) >= 128:
                    issue("long_typed_text", rid, len(action["text"]))
            if annotations and source["segment_index"] < len(annotations):
                annotation = annotations[source["segment_index"]]
                if not annotation["start_s"] <= t + first_dt < annotation["end_s"] + .001:
                    issue("first_action_outside_annotation", rid, {"t": t + first_dt, "start": annotation["start_s"], "end": annotation["end_s"]})
                if annotation.get("intent") != row.get("intent"):
                    issue("intent_source_mismatch", rid)
            if previous and previous["actions"][-1]["type"] == "type_text" and actions[0]["type"] == "type_text":
                prev = previous["actions"][-1]
                gap = t + first_dt - previous["observation"]["timestamp_s"] - prev["dt"]
                if 0 <= gap <= TYPING_GAP_S * max(1, len(prev["text"])):
                    counts["legacy_typing_merge_candidates"] += 1
                    if gap > TYPING_GAP_S:
                        issue("legacy_typing_merge_gap_above_threshold", rid, {"previous": previous["id"], "gap_s": gap, "previous_text": prev["text"][:80], "next_text": actions[0]["text"][:80]})
                    if source["segment_index"] != previous["source"]["segment_index"] + 1:
                        issue("legacy_typing_merge_across_skipped_segments", rid, previous["id"])
            previous = row
            image_paths = row.get("images", [])
            if len(image_paths) != 1:
                issue("image_count", rid, len(image_paths))
                continue
            image_path = directory / image_paths[0]
            if not image_path.exists():
                issue("missing_image", rid, str(image_path))
                continue
            if observation.get("image") != image_paths[0]:
                issue("observation_image_mismatch", rid)
            local_frames += 1
            try:
                with Image.open(image_path) as image:
                    iw, ih = image.size
                    screen = observation["screen"]
                    sw, sh = screen["width"], screen["height"]
                    dimensions[f"{sw}x{sh} -> {iw}x{ih}"] += 1
                    if abs((iw / ih) / (sw / sh) - 1) > .02:
                        issue("screen_image_aspect_mismatch", rid, {"screen": [sw, sh], "image": [iw, ih]})
                    image.verify()
                if row["split"] == "train" and (int(hashlib.sha256(rid.encode()).hexdigest()[:8], 16) % 101 == 0 or len(review_images[software]) < 3):
                    with Image.open(image_path) as image:
                        image.load()
                        preview = image.convert("RGB").resize((64, 36))
                        stats = ImageStat.Stat(preview)
                        counts["images_fully_decoded"] += 1
                        if max(stats.stddev) < 2:
                            issue("near_uniform_sampled_frame", rid, stats.mean)
                        fingerprint = hashlib.sha256(preview.tobytes()).hexdigest()
                        if fingerprint in seen_frames:
                            counts["repeated_sampled_frame_within_workflow"] += 1
                        seen_frames[fingerprint] = rid
                        if len(review_images[software]) < 3:
                            review_images[software].append({"id": rid, "image": str(image_path), "intent": row["intent"], "actions": actions[:1], "screen": screen})
            except Exception as exc:
                issue("unreadable_image", rid, str(exc))
            policy, reason = convert_record(row)
            if policy is None:
                rejections[f"policy:{(reason or 'unknown').split(':')[0]}"] += 1
                per_app[software][f"rejected:{(reason or 'unknown').split(':')[0]}"] += 1
            else:
                counts["policy_rows"] += 1
                counts["supervised_actions"] += len(policy["actions"])
                per_app[software][f"policy:{row['split']}"] += 1
                targets.update(a["type"] for a in policy["actions"])
                _, error = parse_completion(policy["completion"])
                if error:
                    issue("invalid_policy_completion", rid, error)
        if local_frames != manifest["frames"]["count"]:
            issue("frame_manifest_count", key, {"rows_with_frames": local_frames, "manifest": manifest["frames"]["count"]})
        if ordinal % 25 == 0:
            print(f"Audited {counts['completed_shards']} shards / {counts['rows']} rows", flush=True)

    def cross_split(groups):
        return [sorted(group) for group in groups.values() if len(group) > 1 and len(set().union(*(workflow_splits[key] for key in group))) > 1]

    result = {"snapshot_unix": started, "elapsed_s": round(time.time() - started, 1), "counts": counts,
              "issues": issues, "examples": samples, "apps": apps, "splits": splits, "target_types": targets,
              "rejections": rejections, "quality_scores": quality, "modifiers": modifiers, "scroll_deltas": scroll,
              "dimensions": dimensions, "per_app": per_app,
              "workflow_split_leakage": {k: sorted(v) for k, v in workflow_splits.items() if len(v) > 1},
              "duplicate_video_cross_split": cross_split(clip_groups),
              "duplicate_cad_file_cross_split": cross_split(deliverable_groups),
              "identical_task_cross_split": cross_split(task_groups), "visual_review_sample": review_images}
    (args.output / "quality.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("counts", "issues", "target_types", "rejections", "elapsed_s")}, indent=2))


if __name__ == "__main__":
    main()
