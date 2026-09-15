"""Fail-closed integrity, alignment, action coverage and project-split gate for v3 shards."""
import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cad1000.aligned import FRAME_MARGIN_S, MAX_FRAME_AGE_S, RECIPE
from cad1000.policy_view import convert_record
from cad1000.shards import completed_shards, frames_digest, sha256_file, write_json_atomic
from cad1000.trajectory_view import log_line, validate_trajectory_records


def audit_trajectory(folder, expected, split):
    """Bind the actual training labels/images to their already-validated atomic rows."""
    path = folder / "trajectory/all.jsonl"
    validate_trajectory_records([path])
    errors, seen = [], []
    episodes = defaultdict(list)
    for rid, source in expected.items():
        episodes[source.get("_episode_id", 0)].append(rid)
    canonical = {name: hashlib.sha256() for name in ("train", "validation", "test")}
    for line in path.open():
        row = json.loads(line)
        if row["split"] != split:
            errors.append(f"{row['id']}: trajectory split changed")
        canonical[row["split"]].update(line.encode())
        first_source = expected.get(row["steps"][0]["id"])
        if first_source and "first_step_number" in row:
            episode = episodes[first_source.get("_episode_id", 0)]
            start = row["first_step_number"] - 1
            omitted = row["omitted_history_steps"]
            expected_history = [log_line(i + 1, expected[rid]) for i, rid in enumerate(episode) if omitted <= i < start]
            if row["total_steps"] != len(episode) or [s["id"] for s in row["steps"]] != episode[start:start + len(row["steps"])]:
                errors.append(f"{row['id']}: trajectory order or episode boundary changed")
            if row["history_lines"] != expected_history:
                errors.append(f"{row['id']}: history differs from prior executed source steps")
            for field in ("software", "application", "task", "screen"):
                if row[field] != first_source[field]:
                    errors.append(f"{row['id']}: trajectory {field} changed")
        for step, image in zip(row["steps"], row["images"], strict=True):
            rid = step["id"]
            seen.append(rid)
            source = expected.get(rid)
            if source is None or any(step[k] != source[k] for k in ("actions", "completion", "intent")):
                errors.append(f"{rid}: trajectory target differs from atomic source")
            elif (path.parent / image).resolve() != (folder / source["image"]).resolve():
                errors.append(f"{rid}: trajectory image differs from atomic source")
    if seen != list(expected):
        errors.append(f"{folder}: trajectory lost, duplicated or reordered atomic actions")
    for split_name, digest in canonical.items():
        if sha256_file(path.parent / f"{split_name}.jsonl") != digest.hexdigest():
            errors.append(f"{folder}: trajectory {split_name} file differs from all.jsonl")
    return errors


def audit(root, splits, expected, *, full_hashes=True):
    from PIL import Image
    plan = json.loads(splits.read_text())
    shards = completed_shards(root)
    errors, ids, groups = [], set(), defaultdict(set)
    counts, types, apps = Counter(), Counter(), Counter()
    samples = {}
    build = json.loads((root / "build.json").read_text())
    for folder, manifest in shards:
        expected_actions = {}
        key = f"{manifest['software']}/{manifest['workflow_id']}"
        assignment = plan["assignments"][key]
        if manifest["build"] != build or manifest["prepare_report"]["recipe"] != RECIPE or build["split_sha256"] != sha256_file(splits):
            errors.append(f"{key}: mixed recipe/split")
        if full_hashes and frames_digest(folder / "frames") != (manifest["frames"]["count"], manifest["frames"]["sha256"]):
            errors.append(f"{key}: changed frame bytes")
        for name, digest in manifest["checksums"].items():
            if sha256_file(folder / name) != digest:
                errors.append(f"{key}: changed {name}")
        for line in (folder / "all.jsonl").open():
            row = json.loads(line)
            rid = row["id"]
            if rid in ids or row["split"] != assignment["split"]:
                errors.append(f"{rid}: duplicate or wrong split")
            ids.add(rid)
            groups[assignment["project_group"]].add(row["split"])
            source, observation = row["source"], row["observation"]
            t = observation["timestamp_s"]
            start = source["action_start_s"]
            if not source["previous_input_end_s"] + FRAME_MARGIN_S <= t <= start - FRAME_MARGIN_S or start - t > MAX_FRAME_AGE_S:
                errors.append(f"{rid}: ambiguous frame timing")
            if len(row["actions"]) != 1 or abs(row["actions"][0]["dt"] - (start - t)) > 1e-5:
                errors.append(f"{rid}: changed action/frame pairing")
            policy, reason = convert_record(row, drag_mode="include")
            if policy is None:
                errors.append(f"{rid}: {reason}")
            else:
                policy["_episode_id"] = source["episode_id"]
                expected_actions[rid] = policy
            path = folder / row["images"][0]
            with Image.open(path) as image:
                image.verify()
            kind = row["actions"][0]["type"]
            counts[row["split"]] += 1
            types[kind] += 1
            apps[manifest["software"]] += 1
            if row["split"] == "train":
                bucket = (manifest["software"], kind)
                score = hashlib.sha256(rid.encode()).hexdigest()
                if bucket not in samples or score < samples[bucket]["score"]:
                    samples[bucket] = {"score": score, "id": rid, "image": str(path), "intent": row["intent"], "actions": row["actions"], "software": manifest["software"]}
        errors.extend(audit_trajectory(folder, expected_actions, assignment["split"]))
    if any(len(s) > 1 for s in groups.values()):
        errors.append("project family leakage")
    if len(shards) != expected:
        errors.append(f"{len(shards)} completed workflows != expected {expected}")
    if expected == 597 and build["max_actions"] is not None:
        errors.append("Full build is action-capped")
    if expected >= 10 and set(types) != {"click", "drag", "scroll", "key", "type_text"}:
        errors.append("Missing action type coverage")
    return {"status": "passed" if not errors else "failed", "errors": errors[:100], "error_count": len(errors),
            "workflows": len(shards), "rows": len(ids), "split_counts": counts, "types": types, "apps": apps,
            "build": build, "coverage_limitations": plan["coverage_limitations"],
            "visual_review_candidates": list(samples.values()), "semantic_signoff": "separate_review_required"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=Path("data/splits-v3.json"))
    parser.add_argument("--expected", type=int, default=597)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.shards, args.splits, args.expected)
    write_json_atomic(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "visual_review_candidates"}, indent=2))
    return int(result["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
