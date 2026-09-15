#!/usr/bin/env python
"""Offline evaluator: greedy, thinking-disabled generation scored against held-out targets.

    # base model
    python training/evaluate.py --config training/configs/lora_bf16.yaml --split validation.jsonl --output runs/eval/base
    # adapter, with base comparison
    python training/evaluate.py --config ... --adapter runs/lora-bf16/adapter --baseline-metrics runs/eval/base/metrics.json --output runs/eval/lora
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cad_policy.config import load_config, resolve_path  # noqa: E402
from cad_policy.data import dump_json, load_policy_dataset  # noqa: E402
from cad_policy.manifest import build_manifest, data_fingerprint, write_manifest  # noqa: E402
from cad_policy.metrics import aggregate_by_group, score_prediction  # noqa: E402
from cad_policy.model import load_base_model, load_processor  # noqa: E402

END_TOKEN = "<|im_end|>"


def left_pad_batch(examples: list[dict], pad_token_id: int) -> dict[str, torch.Tensor]:
    length = max(e["input_ids"].numel() for e in examples)
    input_ids = torch.full((len(examples), length), pad_token_id, dtype=torch.long)
    attention_mask = torch.zeros((len(examples), length), dtype=torch.long)
    has_mm_types = all("mm_token_type_ids" in e for e in examples)
    mm_token_type_ids = torch.zeros((len(examples), length), dtype=torch.long)
    for row, example in enumerate(examples):
        n = example["input_ids"].numel()
        input_ids[row, length - n :] = example["input_ids"]
        attention_mask[row, length - n :] = 1
        if has_mm_types:
            mm_token_type_ids[row, length - n :] = example["mm_token_type_ids"]
    batch = {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "pixel_values": torch.cat([e["pixel_values"] for e in examples], dim=0),
        "image_grid_thw": torch.cat([e["image_grid_thw"] for e in examples], dim=0),
    }
    if has_mm_types:
        batch["mm_token_type_ids"] = mm_token_type_ids
    return batch


def compare(baseline: dict, current: dict) -> dict:
    keys = ["json_parse_rate", "schema_valid_rate", "action_type_accuracy", "exact_match", "coord_error_px_mean", "coord_error_px_median"]
    delta = {}
    for scope in ("overall", "macro"):
        delta[scope] = {}
        for key in keys:
            a, b = baseline.get(scope, {}).get(key), current.get(scope, {}).get(key)
            delta[scope][key] = {"baseline": a, "current": b, "delta": (b - a) if a is not None and b is not None else None}
        delta[scope]["within_px"] = {
            r: {"baseline": baseline.get(scope, {}).get("within_px", {}).get(r), "current": v}
            for r, v in current.get(scope, {}).get("within_px", {}).items()
        }
    return delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--adapter", default=None, help="PEFT adapter directory; omit for the base model")
    parser.add_argument("--split", default="validation.jsonl")
    parser.add_argument("--max-examples", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-metrics", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=None)
    parser.add_argument("--policy-dir", default=None, help="Override data.policy_dir")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.policy_dir:
        config["data"]["policy_dir"] = args.policy_dir
    output_dir = resolve_path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_size = args.batch_size or int(config["eval"]["batch_size"])
    max_new_tokens = args.max_new_tokens or int(config["eval"]["max_new_tokens"])
    radii = tuple(int(r) for r in config["eval"]["radii_px"])

    processor = load_processor(config["model"])
    processor.tokenizer.padding_side = "left"
    max_examples = args.max_examples if args.max_examples is not None else config["data"]["eval_max_examples"]
    dataset = load_policy_dataset(config, processor, args.split, max_examples=max_examples)
    if len(dataset) == 0:
        raise SystemExit(f"no examples in {args.split}")
    model = load_base_model(config["model"], training=False)
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()
    device = next(model.parameters()).device
    pad_id = processor.tokenizer.pad_token_id
    end_id = processor.tokenizer.convert_tokens_to_ids(END_TOKEN)

    predictions, scores, groups = [], [], []
    started = time.time()
    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for start in range(0, len(dataset), batch_size):
            indices = list(range(start, min(start + batch_size, len(dataset))))
            batch = left_pad_batch([dataset.encode(i, with_completion=False) for i in indices], pad_id)
            batch = {k: v.to(device) for k, v in batch.items()}
            with torch.no_grad():
                generated = model.generate(**batch, max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=pad_id, eos_token_id=[end_id, processor.tokenizer.eos_token_id])
            new_tokens = generated[:, batch["input_ids"].shape[1] :]
            texts = processor.tokenizer.batch_decode(new_tokens, skip_special_tokens=True)
            for index, text in zip(indices, texts, strict=True):
                record = dataset.target(index)
                score = score_prediction(text, record["actions"], record["screen"], radii_px=radii)
                scores.append(score)
                groups.append(str(record["software"]))
                item = {"id": record["id"], "software": record["software"], "intent": record["intent"], "history_steps": record["history_steps"], "target": record["completion"], "prediction": text, "score": score}
                predictions.append(item)
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            done = len(scores)
            if done % (batch_size * 10) == 0 or done == len(dataset):
                print(f"{done}/{len(dataset)} scored, {time.time() - started:.0f}s", flush=True)
    metrics = aggregate_by_group(scores, groups, radii_px=radii)
    metrics["elapsed_s"] = round(time.time() - started, 1)
    metrics["adapter"] = args.adapter
    metrics["split"] = args.split
    metrics["examples"] = len(dataset)
    if args.baseline_metrics:
        baseline = json.loads(Path(args.baseline_metrics).read_text(encoding="utf-8"))
        metrics["comparison_to_baseline"] = compare(baseline, metrics)
    dump_json(output_dir / "metrics.json", metrics)
    write_manifest(output_dir / "eval_manifest.json", build_manifest("evaluate", config, adapter=args.adapter, split=args.split, data=data_fingerprint(resolve_path(config["data"]["policy_dir"]), [args.split]), metrics=metrics))
    print(json.dumps({"overall": metrics["overall"], "macro": metrics["macro"]}, indent=2))
    for item in predictions[:5]:
        print(f"--- {item['id']}\n  target:     {item['target']}\n  prediction: {item['prediction']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
