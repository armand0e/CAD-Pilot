"""Read-only follow-up audit of the merged view and a retained raw-event workflow.

Run: PYTHONPATH=src .venv/bin/python scripts/audit_corpus_alignment.py --output runs/corpus-audit-2026-09-08
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

from cad1000.policy_view import convert_record
from cad1000.prepare import compact_actions, load_event_index, merge_split_typing
from cad1000.shards import frames_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", action="store_true", help="Rehash every completed shard's JPEG bytes")
    args = parser.parse_args()
    counts, examples = Counter(), defaultdict(list)
    if args.frames:
        for path in sorted(Path("data/shards").glob("*/*/manifest.json")):
            manifest = json.loads(path.read_text())
            if manifest.get("status") != "complete":
                continue
            count, digest = frames_digest(path.parent / "frames")
            counts["shards"] += 1
            counts["frames"] += count
            if count != manifest["frames"]["count"] or digest != manifest["frames"]["sha256"]:
                examples["mismatch"].append(str(path))
        result = {"counts": counts, "examples": examples}
        destination = args.output / "frame-integrity.json"
    else:
        for line in (args.output / "corpus/all.jsonl").open():
            row = json.loads(line)
            counts["merged_rows"] += 1
            policy, _ = convert_record(row)
            if not policy:
                continue
            counts["policy_rows"] += 1
            actions = policy["actions"]
            if actions[0]["type"] == "scroll" and actions[0]["delta_x"] == actions[0]["delta_y"] == 0:
                counts["zero_scroll_targets"] += 1
                raw = next(a for a in row["actions"] if a["type"] == "scroll")
                counts["zero_scroll_original_" + ("positive" if raw["delta_y"] > 0 else "negative" if raw["delta_y"] < 0 else "zero")] += 1
                if len(examples["zero_scroll"]) < 5:
                    examples["zero_scroll"].append({"id": row["id"], "source": raw, "target": actions[0]})
            if row["actions"][0]["dt"] > 1:
                counts["first_target_over_one_second_after_frame"] += 1
                if len(examples["stale_frame"]) < 5:
                    examples["stale_frame"].append({"id": row["id"], "first_action": row["actions"][0]})
            if any(token in json.dumps(row, ensure_ascii=False) for token in ("<|im_start|>", "<|im_end|>", "<|image_pad|>")):
                counts["chat_control_token_literal"] += 1
            intent = row["intent"].lower()
            terms = ("system settings", "screen recording", "recording software", "volume", "wi-fi", "brightness")
            if any(term in intent for term in terms):
                counts["off_task_intent_review_candidates"] += 1
                if len(examples["off_task_review"]) < 12:
                    examples["off_task_review"].append({"id": row["id"], "intent": row["intent"]})

        workflow = "solidworks/01a90368-0e9d-420b-99f2-0e3ec22c1638"
        raw_dir = Path("data/raw") / workflow
        original = [json.loads(line) for line in (Path("data/shards") / workflow / "all.jsonl").open()]
        manifest = json.loads((Path("data/shards") / workflow / "manifest.json").read_text())
        index = load_event_index(raw_dir / "events.json", software="solidworks", application="SOLIDWORKS")
        annotations = json.loads((raw_dir / "narration.json").read_text())["annotations"]
        merged, joins = merge_split_typing(copy.deepcopy(original))
        replay = {"workflow": workflow, "original_rows": len(original), "merged_rows": len(merged), "typing_joins": joins, "differences": []}
        for row in merged:
            annotation = annotations[row["source"]["segment_index"]]
            source = index.actions_between(annotation["start_s"], annotation["end_s"])
            expected = compact_actions(source[:manifest["prepare_report"]["parameters"]["max_actions"]], row["observation"]["timestamp_s"])
            if expected != row["actions"]:
                replay["differences"].append({"id": row["id"], "saved": row["actions"], "expected": expected})
        result = {"counts": counts, "examples": examples, "source_replay": replay}
        destination = args.output / "alignment.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(destination), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
