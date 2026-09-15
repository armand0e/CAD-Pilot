#!/usr/bin/env python
"""Preflight: load one real batch, print trainable modules, run forward/backward, report VRAM.

Also verifies (a) the image-token estimate matches the processor exactly, (b) the label mask
covers only the completion, and (c) the model's loss on the batch is finite.

    /home/armand0e/Documents/training/.venv/bin/python training/preflight.py --config training/configs/lora_bf16.yaml
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
from cad_policy.data import IGNORE_INDEX, PolicyCollator, describe_mask, dump_json, load_policy_dataset  # noqa: E402
from cad_policy.loss import sparse_lm_loss  # noqa: E402
from cad_policy.manifest import build_manifest, write_manifest  # noqa: E402
from cad_policy.model import attach_lora, load_base_model, load_processor, peak_vram_gib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--batch-size", type=int, default=None, help="Override train.per_device_batch")
    parser.add_argument("--samples", type=int, default=8, help="Examples to cross-check image-token estimates on")
    parser.add_argument("--steps", type=int, default=2, help="Forward/backward iterations to time")
    parser.add_argument("--output", default=None, help="Directory for preflight artifacts")
    parser.add_argument("--policy-dir", default=None, help="Override data.policy_dir")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.policy_dir:
        config["data"]["policy_dir"] = args.policy_dir
    batch_size = args.batch_size or int(config["train"]["per_device_batch"])
    output_dir = resolve_path(args.output or (Path(config["train"]["output_dir"]) / "preflight"))
    output_dir.mkdir(parents=True, exist_ok=True)

    processor = load_processor(config["model"])
    dataset = load_policy_dataset(config, processor, config["data"]["train_file"], max_examples=max(args.samples, batch_size * args.steps))
    report: dict = {"token_report": dataset.length_report(), "examples": len(dataset)}
    print(json.dumps(report["token_report"], indent=2, default=str), flush=True)

    # (a) exact image-token count vs estimate, (b) prompt/full prefix consistency.
    mismatches = []
    image_token_id = processor.tokenizer.convert_tokens_to_ids("<|image_pad|>")
    for index in range(min(args.samples, len(dataset))):
        encoded = dataset.encode(index)
        actual_image = int((encoded["input_ids"] == image_token_id).sum())
        plan = dataset.plans[index]
        prompt_only = dataset.encode(index, with_completion=False)["input_ids"]
        multi_turn = encoded.get("supervised_spans", 1) > 1
        prefix_ok = torch.equal(prompt_only, encoded["input_ids"][: prompt_only.numel()]) and (multi_turn or prompt_only.numel() == encoded["prompt_length"])
        if actual_image != plan["image_tokens"] or encoded["input_ids"].numel() != plan["estimated_total_tokens"] or not prefix_ok:
            mismatches.append({"id": dataset.records[index]["id"], "estimated": plan, "actual_image_tokens": actual_image, "actual_total": encoded["input_ids"].numel(), "prefix_ok": prefix_ok})
    report["token_estimate_mismatches"] = mismatches
    if mismatches:
        print(json.dumps(mismatches, indent=2), flush=True)
        raise SystemExit("image-token estimate or prompt prefix mismatch; fix before training")
    print(f"image-token estimate exact on {min(args.samples, len(dataset))} samples", flush=True)

    collator = PolicyCollator(processor.tokenizer.pad_token_id)
    batch = collator([dataset[i] for i in range(batch_size)])
    audit = describe_mask(batch, processor.tokenizer)
    report["label_mask_audit"] = audit
    print(json.dumps(audit, indent=2), flush=True)
    assert audit["image_tokens_supervised"] == 0 and audit["supervised_tokens"] > 0, audit
    assert audit["supervised_text"].startswith('{"actions":'), audit["supervised_text"]
    report["supervised_spans"] = [dataset.encode(i).get("supervised_spans", 1) for i in range(min(batch_size, len(dataset)))]
    assert audit["supervised_text"].endswith("<|im_end|>"), audit["supervised_text"]

    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    model = load_base_model(config["model"])
    load_s = time.time() - started
    model, trainable = attach_lora(model, config)
    report["trainable"] = {k: v for k, v in trainable.items() if k != "trainable_parameter_names"}
    report["trainable"]["module_count"] = len(trainable["lora_target_modules"])
    dump_json(output_dir / "trainable_parameters.json", trainable)
    print(f"model loaded in {load_s:.0f}s; trainable {trainable['trainable_parameters']:,} ({trainable['trainable_percent']}%), {len(trainable['lora_target_modules'])} target modules", flush=True)
    print("first targets:", trainable["lora_target_modules"][:6], flush=True)
    report["vram_after_load_gib"] = round(torch.cuda.memory_allocated() / 2**30, 2)

    model.train()
    device = next(model.parameters()).device
    timings = []
    for step in range(args.steps):
        batch = collator([dataset[(step * batch_size + i) % len(dataset)] for i in range(batch_size)])
        batch = {k: v.to(device) for k, v in batch.items()}
        torch.cuda.synchronize()
        t0 = time.time()
        loss = sparse_lm_loss(model, batch)
        loss.backward()
        torch.cuda.synchronize()
        timings.append(time.time() - t0)
        supervised = int((batch["labels"] != IGNORE_INDEX).sum())
        print(f"step {step}: loss={loss.item():.4f} tokens={batch['input_ids'].shape} supervised={supervised} time={timings[-1]:.2f}s peak={peak_vram_gib():.2f} GiB", flush=True)
        if not torch.isfinite(loss):
            raise SystemExit("non-finite loss")
        model.zero_grad(set_to_none=True)
    grad_norms = [(n, float(p.grad.norm())) for n, p in model.named_parameters() if p.requires_grad and p.grad is not None][:3]
    report.update({"loss": float(loss.item()), "step_times_s": timings, "peak_vram_gib": round(peak_vram_gib(), 2), "batch_shape": list(batch["input_ids"].shape), "grad_norm_samples": grad_norms, "model_load_s": round(load_s, 1)})
    write_manifest(output_dir / "preflight_manifest.json", build_manifest("preflight", config, report=report))
    print(json.dumps({k: report[k] for k in ("loss", "step_times_s", "peak_vram_gib", "batch_shape", "vram_after_load_gib")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
