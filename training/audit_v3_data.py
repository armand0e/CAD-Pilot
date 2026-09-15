"""Data-only v3 gate: every window's real token plan, stratified pixels/loss masks.

No model weights, CUDA, optimizer, gradients, or training are used. Streaming rows
through an empty PolicyDataset bounds memory independently of corpus size.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch

from cad1000.shards import completed_shards, sha256_file, write_json_atomic
from cad_policy.config import load_config
from cad_policy.data import PolicyCollator, PolicyDataset
from cad_policy.model import load_processor
from cad_policy.trajectory import render_conversation, trajectory_messages


def sample_buckets(row, plan):
    """Deterministic coverage, not a claimed random semantic-quality estimate."""
    score = hashlib.sha256(row["id"].encode()).hexdigest()
    app = row["software"]
    result = {f"{app}/action/{a['type']}": score for s in row["steps"] for a in s["actions"]}
    result[f"{app}/longest"] = -plan["estimated_total_tokens"]
    result[f"split/{row['split']}"] = score
    result["longest_history"] = -len(row.get("history_lines", []))
    return result


def verify_encoding(dataset, index):
    row, plan = dataset.records[index], dataset.plans[index]
    encoded = dataset.encode(index)
    ids, labels = encoded["input_ids"], encoded["labels"]
    supervised = labels != -100
    expected = "".join(s["completion"] + "<|im_end|>" for s in row["steps"])
    image_id = dataset.tokenizer.convert_tokens_to_ids("<|image_pad|>")
    messages = trajectory_messages(row, plan["variant"])
    prompt = render_conversation(messages, drop_final_assistant=True)
    reference = dataset.processor.apply_chat_template(messages[:-1], tokenize=False,
                                                       add_generation_prompt=True, enable_thinking=False)
    checks = {
        "exact_token_plan": ids.numel() == plan["estimated_total_tokens"],
        "all_steps_supervised": encoded["supervised_spans"] == len(row["steps"]),
        "only_exact_completions_supervised": dataset.tokenizer.decode(ids[supervised]) == expected,
        "image_tokens_masked": not bool(((ids == image_id) & supervised).any()),
        "exact_image_token_count": int((ids == image_id).sum()) == plan["image_tokens"],
        "generation_template_parity": prompt == reference,
        "token_budget": ids.numel() <= dataset.max_total_tokens,
        "cpu_only": all(v.device.type == "cpu" for v in encoded.values() if torch.is_tensor(v)),
    }
    if not all(checks.values()):
        raise ValueError(f"{row['id']}: encoding gate failed: {checks}")
    result = {"id": row["id"], "software": row["software"], "split": row["split"],
              "steps": len(row["steps"]), "tokens": ids.numel(),
              "supervised_tokens": int(supervised.sum()), "checks": checks}
    # Exercise real encoded right-padding without duplicating enormous pixel arrays.
    if index == 0:
        short = {k: v for k, v in encoded.items()}
        for key in ("input_ids", "labels", "attention_mask", "mm_token_type_ids"):
            if key in short:
                short[key] = short[key][:-1]
        batch = PolicyCollator(dataset.tokenizer.pad_token_id)([encoded, short])
        if batch["attention_mask"][1, -1] != 0 or batch["labels"][1, -1] != -100:
            raise ValueError("Padding tokens carry loss")
        result["padding_mask_passed"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--shards", type=Path, required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output / "status.json", {"status": "running", "training_started": False})
    torch.set_num_threads(2)
    config = load_config(args.config)
    processor = load_processor(config["model"])
    data = config["data"]
    empty = args.output / "empty.jsonl"
    empty.write_text("")
    dataset = PolicyDataset(empty, processor, max_total_tokens=data["max_total_tokens"],
                            min_pixels=data["image"]["min_pixels"], max_pixels=data["image"]["max_pixels"],
                            require_full_coverage=True)
    shards = completed_shards(args.shards)
    if len(shards) != args.expected:
        raise ValueError(f"{len(shards)} workflows != {args.expected}")
    selected, totals, lengths, variants = {}, Counter(), Counter(), Counter()
    sources = hashlib.sha256()
    with (args.output / "workflows.jsonl").open("w") as report:
        for folder, manifest in shards:
            path = folder / "trajectory/all.jsonl"
            sources.update(str(path.relative_to(args.shards)).encode())
            sources.update(sha256_file(path).encode())
            dataset.base_dir = path.parent
            counts = Counter()
            for line in path.open():
                row = json.loads(line)
                plan = dataset.plan(row)
                if plan is None or dataset.excluded or plan.get("steps_dropped", 0):
                    raise ValueError(f"{row['id']}: omitted row or action: {plan}; {dataset.excluded}")
                if plan["variant_index"] != 0:
                    raise ValueError(f"{row['id']}: full task description/requirements do not fit")
                counts["windows"] += 1
                counts["steps"] += len(row["steps"])
                totals[f"{row['split']}_windows"] += 1
                totals[f"{row['split']}_steps"] += len(row["steps"])
                lengths[plan["estimated_total_tokens"]] += 1
                variants[plan["variant_index"]] += 1
                for bucket, score in sample_buckets(row, plan).items():
                    if bucket not in selected or score < selected[bucket][0]:
                        resolved = row | {"images": [str((path.parent / p).resolve()) for p in row["images"]]}
                        selected[bucket] = (score, resolved, plan)
            if counts["steps"] != manifest["prepare_report"]["example_count"]:
                raise ValueError(f"{folder}: trajectory coverage does not match accepted atomic rows")
            result = {"workflow": str(folder.relative_to(args.shards)), **counts}
            report.write(json.dumps(result) + "\n")
            report.flush()
            print(json.dumps(result), flush=True)
    unique = {entry[1]["id"]: entry[1:] for entry in selected.values()}
    # Small example first keeps the real collator test's peak memory small.
    ordered = sorted(unique.values(), key=lambda value: (value[1]["estimated_total_tokens"], value[0]["id"]))
    dataset.records = [row for row, _ in ordered]
    dataset.plans = [plan for _, plan in ordered]
    results = []
    for index in range(len(dataset)):
        result = verify_encoding(dataset, index)
        results.append(result)
        print(json.dumps(result), flush=True)
    result = {"status": "passed", "training_started": False, "model_weights_loaded": False,
              "build": json.loads((args.shards / "build.json").read_text()),
              "model": {k: config["model"][k] for k in ("name", "revision")},
              "data": data, "workflows": len(shards), "counts": totals,
              "trajectory_sha256": sources.hexdigest(), "prompt_variants": variants,
              "token_range": {"min": min(lengths), "max": max(lengths)},
              "excluded_rows": 0, "dropped_steps": 0,
              "pixel_sampling": "Minimum SHA256 per app/action and split; longest window per app and longest history",
              "pixel_buckets": {k: v[1]["id"] for k, v in sorted(selected.items())}, "pixel_results": results}
    write_json_atomic(args.output / "processor.json", result)
    write_json_atomic(args.output / "status.json", {"status": "passed", "training_started": False})


if __name__ == "__main__":
    main()
