#!/usr/bin/env python
"""Render a Markdown report for a training run (loss curve, memory, throughput, predictions).

    python training/report_run.py --run runs/overfit32 [--eval runs/eval/overfit32] [--baseline runs/eval/base]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cad_policy.config import resolve_path  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return "–"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def metrics_table(name: str, metrics: dict) -> list[str]:
    overall, macro = metrics.get("overall", {}), metrics.get("macro", {})
    rows = [
        ("examples", overall.get("count"), None),
        ("strict JSON parse rate", overall.get("json_parse_rate"), macro.get("json_parse_rate")),
        ("schema-valid rate", overall.get("schema_valid_rate"), macro.get("schema_valid_rate")),
        ("action-type accuracy", overall.get("action_type_accuracy"), macro.get("action_type_accuracy")),
        ("exact match", overall.get("exact_match"), macro.get("exact_match")),
        ("key/modifier accuracy", overall.get("key_accuracy"), None),
        ("type_text accuracy", overall.get("type_text_accuracy"), None),
        ("click error px (mean / median)", f"{fmt(overall.get('coord_error_px_mean'), 1)} / {fmt(overall.get('coord_error_px_median'), 1)}", f"{fmt(macro.get('coord_error_px_mean'), 1)} / {fmt(macro.get('coord_error_px_median'), 1)}"),
    ]
    for radius, value in (overall.get("within_px") or {}).items():
        rows.append((f"within {radius} px", value, (macro.get("within_px") or {}).get(radius)))
    lines = [f"### {name}", "", "| metric | overall | macro (per app) |", "|---|---|---|"]
    lines.extend(f"| {label} | {fmt(a)} | {fmt(b)} |" for label, a, b in rows)
    per_group = metrics.get("per_group", {})
    if len(per_group) > 1:
        lines += ["", "| application | n | JSON | type acc | exact | px median |", "|---|---|---|---|---|---|"]
        lines.extend(
            f"| {app} | {m.get('count')} | {fmt(m.get('json_parse_rate'))} | {fmt(m.get('action_type_accuracy'))} | {fmt(m.get('exact_match'))} | {fmt(m.get('coord_error_px_median'), 1)} |"
            for app, m in per_group.items()
        )
    return lines + [""]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--eval", default=None, help="Evaluation directory for the adapter")
    parser.add_argument("--baseline", default=None, help="Evaluation directory for the base model")
    parser.add_argument("--examples", type=int, default=6)
    args = parser.parse_args()
    run = resolve_path(args.run)
    summary = load(run / "train_summary.json")
    config = load(run / "config.resolved.json")
    trainable = load(run / "trainable_parameters.json")
    tokens = load(run / "token_report.json")
    audit = load(run / "label_mask_audit.json")
    history = [h for h in summary.get("log_history", []) if "loss" in h]
    losses = [h["loss"] for h in history]
    train_metrics = summary.get("train_result", {})
    lines = [f"# Run report: `{run.name}`", ""]
    lines += ["## Configuration", "", "| key | value |", "|---|---|"]
    for key, value in (
        ("model", config.get("model", {}).get("name")),
        ("quantization", config.get("model", {}).get("quantization")),
        ("LoRA r / alpha / dropout", f"{config.get('lora', {}).get('r')} / {config.get('lora', {}).get('alpha')} / {config.get('lora', {}).get('dropout')}"),
        ("trainable params", f"{trainable.get('trainable_parameters', 0):,} ({trainable.get('trainable_percent')}%), vision trainable {trainable.get('vision_trainable_parameters')}"),
        ("LoRA modules", len(trainable.get("lora_target_modules", []))),
        ("examples", summary.get("examples")),
        ("epochs / effective batch / lr", f"{config.get('train', {}).get('epochs')} / {config.get('train', {}).get('per_device_batch', 1) * config.get('train', {}).get('grad_accum', 1)} / {config.get('train', {}).get('lr')}"),
        ("token budget", config.get("data", {}).get("max_total_tokens")),
        ("seed", config.get("train", {}).get("seed")),
    ):
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Data", ""]
    train_tokens = tokens.get("train") or {}
    lines.append(f"- total tokens per example: {train_tokens.get('total_tokens')}")
    lines.append(f"- image tokens: {train_tokens.get('image_tokens')}; completion tokens: {train_tokens.get('completion_tokens')}")
    lines.append(f"- prompt variants used: {train_tokens.get('prompt_variant_counts')}; excluded: {train_tokens.get('excluded')}")
    lines.append(f"- applications: {train_tokens.get('software_counts')}")
    lines.append(f"- label-mask audit: {audit.get('supervised_tokens')} supervised tokens, {audit.get('image_tokens_supervised')} image tokens supervised; supervised text `{audit.get('supervised_text')}`")
    lines += ["", "## Training", ""]
    if losses:
        lines.append(f"- loss: first {losses[0]:.4f}, min {min(losses):.4f}, last {losses[-1]:.4f} over {len(losses)} logged steps")
        step = max(1, len(history) // 12)
        lines += ["", "| step | epoch | loss | grad norm | lr |", "|---|---|---|---|---|"]
        for h in history[::step] + ([history[-1]] if len(history) % step else []):
            lines.append(f"| {h.get('step')} | {fmt(h.get('epoch'), 2)} | {h['loss']:.4f} | {fmt(h.get('grad_norm'), 3)} | {h.get('learning_rate'):.2e} |")
    evals = [h for h in summary.get("log_history", []) if "eval_loss" in h]
    if evals:
        lines += ["", "| step | eval loss |", "|---|---|"] + [f"| {h.get('step')} | {h['eval_loss']:.4f} |" for h in evals]
    runtime = train_metrics.get("train_runtime")
    lines += [
        "",
        f"- wall time: {fmt(summary.get('elapsed_s'), 0)} s (trainer runtime {fmt(runtime, 0)} s)",
        f"- throughput: {fmt(train_metrics.get('train_samples_per_second'), 3)} samples/s, {fmt(train_metrics.get('train_steps_per_second'), 4)} optimizer steps/s",
        f"- peak VRAM: {summary.get('peak_vram_gib')} GiB",
        f"- adapter: `{summary.get('adapter_dir')}`",
        "",
    ]
    if args.baseline:
        lines += metrics_table("Base model (same records)", load(resolve_path(args.baseline) / "metrics.json"))
    if args.eval:
        eval_dir = resolve_path(args.eval)
        lines += metrics_table("Adapter", load(eval_dir / "metrics.json"))
        predictions = eval_dir / "predictions.jsonl"
        if predictions.exists():
            lines += ["### Example predictions", ""]
            for line in predictions.read_text(encoding="utf-8").splitlines()[: args.examples]:
                item = json.loads(line)
                lines += [f"- `{item['id']}` — intent: {item.get('intent')}", f"  - target: `{item['target']}`", f"  - prediction: `{item['prediction']}`"]
            lines.append("")
    report = run / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
