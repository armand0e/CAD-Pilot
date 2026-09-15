#!/usr/bin/env python
"""Qwen3.5-9B BF16 LoRA (or NF4 QLoRA) fine-tune for the CAD visual-action policy.

Usage:
    cd /home/armand0e/Documents/cad-model
    /home/armand0e/Documents/training/.venv/bin/python training/train_qwen35_cad.py \
        --config training/configs/lora_bf16.yaml [--resume] [--max-examples N]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cad_policy.config import dump_config, load_config, resolve_path  # noqa: E402
from cad_policy.data import PolicyCollator, describe_mask, dump_json, load_policy_dataset  # noqa: E402
from cad_policy.loss import sparse_lm_loss  # noqa: E402
from cad_policy.manifest import build_manifest, data_fingerprint, write_manifest  # noqa: E402
from cad_policy.model import attach_lora, load_base_model, load_processor, peak_vram_gib  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--resume", nargs="?", const=True, default=False, help="Resume from last (or given) checkpoint")
    parser.add_argument("--max-examples", type=int, help="Override data.max_examples")
    parser.add_argument("--output-dir", help="Override train.output_dir")
    parser.add_argument("--no-eval", action="store_true", help="Disable evaluation during training")
    parser.add_argument("--policy-dir", default=None, help="Override data.policy_dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.policy_dir:
        config["data"]["policy_dir"] = args.policy_dir
    if args.max_examples is not None:
        config["data"]["max_examples"] = args.max_examples
    if args.output_dir:
        config["train"]["output_dir"] = args.output_dir
    if args.no_eval:
        config["train"]["eval_during_training"] = False
    train_cfg = config["train"]
    output_dir = resolve_path(train_cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.resolved.json").write_text(dump_config(config) + "\n", encoding="utf-8")

    from transformers import Trainer, TrainingArguments, set_seed

    set_seed(int(train_cfg["seed"]))
    processor = load_processor(config["model"])
    train_dataset = load_policy_dataset(config, processor, config["data"]["train_file"], max_examples=config["data"]["max_examples"])
    if len(train_dataset) == 0:
        raise SystemExit("no training examples after budget filtering")
    eval_dataset = None
    eval_path = resolve_path(config["data"]["policy_dir"]) / config["data"]["eval_file"]
    if train_cfg["eval_during_training"] and eval_path.exists() and eval_path.stat().st_size > 0:
        eval_dataset = load_policy_dataset(config, processor, config["data"]["eval_file"], max_examples=config["data"]["eval_max_examples"])
        if len(eval_dataset) == 0:
            eval_dataset = None
    length_report = {"train": train_dataset.length_report(), "eval": eval_dataset.length_report() if eval_dataset else None}
    dump_json(output_dir / "token_report.json", length_report)
    print(json.dumps(length_report, indent=2, default=str), flush=True)

    model = load_base_model(config["model"])
    model, trainable = attach_lora(model, config)
    dump_json(output_dir / "trainable_parameters.json", trainable)
    print(
        f"trainable: {trainable['trainable_parameters']:,} / {trainable['total_parameters']:,} "
        f"({trainable['trainable_percent']}%), vision trainable: {trainable['vision_trainable_parameters']}",
        flush=True,
    )

    collator = PolicyCollator(processor.tokenizer.pad_token_id)
    audit = describe_mask(collator([train_dataset[0]]), processor.tokenizer)
    dump_json(output_dir / "label_mask_audit.json", audit)
    if audit["image_tokens_supervised"] != 0 or audit["supervised_tokens"] == 0:
        raise SystemExit(f"label mask audit failed: {audit}")
    print(f"label mask audit: {audit['supervised_tokens']} supervised tokens -> {audit['supervised_text']!r}", flush=True)

    weights = torch.tensor(train_dataset.sampling_weights(float(config["data"]["app_temperature"])), dtype=torch.double)

    class WeightedTrainer(Trainer):
        model_accepts_loss_kwargs = True  # loss below is normalised by num_items_in_batch

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            loss = sparse_lm_loss(model, inputs, num_items_in_batch=num_items_in_batch)
            return (loss, {"loss": loss}) if return_outputs else loss

        def _get_train_sampler(self, *_args, **_kwargs):
            generator = torch.Generator().manual_seed(int(train_cfg["seed"]))
            if config["data"].get("sampling") == "coverage":
                return torch.utils.data.RandomSampler(train_dataset, replacement=False, generator=generator)
            return torch.utils.data.WeightedRandomSampler(weights, num_samples=len(train_dataset), replacement=True, generator=generator)

    steps_per_epoch = math.ceil(len(train_dataset) / (train_cfg["per_device_batch"] * train_cfg["grad_accum"]))
    total_steps = int(train_cfg["max_steps"]) if int(train_cfg["max_steps"]) > 0 else math.ceil(steps_per_epoch * float(train_cfg["epochs"]))
    warmup_steps = math.ceil(float(train_cfg["warmup_ratio"]) * total_steps)
    arguments = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=float(train_cfg["epochs"]),
        max_steps=int(train_cfg["max_steps"]),
        per_device_train_batch_size=int(train_cfg["per_device_batch"]),
        per_device_eval_batch_size=int(train_cfg["per_device_batch"]),
        gradient_accumulation_steps=int(train_cfg["grad_accum"]),
        learning_rate=float(train_cfg["lr"]),
        lr_scheduler_type=train_cfg["scheduler"],
        warmup_steps=warmup_steps,
        weight_decay=float(train_cfg["weight_decay"]),
        max_grad_norm=float(train_cfg["max_grad_norm"]),
        optim=train_cfg["optim"],
        bf16=True,
        fp16=False,
        logging_steps=int(train_cfg["logging_steps"]),
        logging_first_step=True,
        save_strategy="steps",
        save_steps=int(train_cfg["save_steps"]),
        save_total_limit=int(train_cfg["save_total_limit"]),
        eval_strategy="steps" if eval_dataset is not None else "no",
        eval_steps=int(train_cfg["eval_steps"]),
        seed=int(train_cfg["seed"]),
        data_seed=int(train_cfg["seed"]),
        dataloader_num_workers=int(train_cfg["dataloader_workers"]),
        dataloader_prefetch_factor=(int(train_cfg.get("dataloader_prefetch", 2)) if int(train_cfg["dataloader_workers"]) > 0 else None),
        dataloader_pin_memory=bool(train_cfg.get("dataloader_pin_memory", True)),
        remove_unused_columns=False,
        gradient_checkpointing=False,  # enabled explicitly in attach_lora with use_reentrant=False
        report_to=train_cfg["report_to"],
        label_names=["labels"],
    )
    trainer = WeightedTrainer(
        model=model,
        args=arguments,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=collator,
        processing_class=processor,
    )
    manifest = build_manifest(
        "train",
        config,
        data=data_fingerprint(resolve_path(config["data"]["policy_dir"]), [config["data"]["train_file"], config["data"]["eval_file"]]),
        trainable=trainable | {"trainable_parameter_names": f"see trainable_parameters.json ({len(trainable['trainable_parameter_names'])} tensors)"},
        token_report=length_report,
        label_mask_audit=audit,
        steps_per_epoch=steps_per_epoch,
        total_steps=total_steps,
        warmup_steps=warmup_steps,
        model_snapshot=str(getattr(model.base_model.model.config, "_name_or_path", config["model"]["name"])),
    )
    write_manifest(output_dir / "run_manifest.json", manifest)

    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train(resume_from_checkpoint=args.resume if args.resume else None)
    elapsed = time.time() - started

    adapter_dir = output_dir / "adapter"
    trainer.model.save_pretrained(str(adapter_dir), safe_serialization=True)
    processor.save_pretrained(str(adapter_dir))
    summary = {
        "train_result": result.metrics,
        "elapsed_s": round(elapsed, 1),
        "peak_vram_gib": round(peak_vram_gib(), 2),
        "examples": len(train_dataset),
        "log_history": trainer.state.log_history,
        "adapter_dir": str(adapter_dir),
    }
    dump_json(output_dir / "train_summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "log_history"}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
