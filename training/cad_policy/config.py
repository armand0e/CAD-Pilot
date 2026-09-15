"""YAML experiment configuration with single-level ``extends`` inheritance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from . import REPO_ROOT

DEFAULTS: dict[str, Any] = {
    "model": {
        "name": "Qwen/Qwen3.5-9B",
        "revision": None,
        "dtype": "bfloat16",
        "attn_implementation": "sdpa",
        "quantization": "none",  # none | nf4
    },
    "data": {
        "policy_dir": "data/processed/policy-smoke",
        "train_file": "train.jsonl",
        "eval_file": "validation.jsonl",
        "max_total_tokens": 8192,
        "max_examples": None,
        "eval_max_examples": 512,
        "app_temperature": 0.5,
        "image": {"max_pixels": 1048576, "min_pixels": 65536},
    },
    "lora": {
        "r": 32,
        "alpha": 64,
        "dropout": 0.05,
        "bias": "none",
        "targets": "language_decoder",
        "include_linear_attn_gates": False,
        "include_merger": False,
        "train_merger_full": False,
        "merger_lr": None,
    },
    "train": {
        "output_dir": "runs/default",
        "epochs": 1,
        "max_steps": -1,
        "per_device_batch": 1,
        "grad_accum": 32,
        "lr": 1.0e-4,
        "scheduler": "cosine",
        "warmup_ratio": 0.03,
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
        "optim": "adamw_torch_fused",
        "seed": 42,
        "logging_steps": 5,
        "save_steps": 200,
        "eval_steps": 200,
        "save_total_limit": 3,
        "gradient_checkpointing": True,
        "dataloader_workers": 4,
        "eval_during_training": True,
        "report_to": "none",
    },
    "eval": {"max_new_tokens": 96, "batch_size": 4, "radii_px": [8, 16, 32, 64]},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a mapping")
    parent = loaded.pop("extends", None)
    if parent:
        base = load_config(path.parent / parent)
    else:
        base = copy.deepcopy(DEFAULTS)
    config = _merge(base, loaded)
    config.setdefault("_meta", {})
    config["_meta"]["config_path"] = str(path)
    config["_meta"]["sources"] = [*config["_meta"].get("sources", []), str(path)]
    return config


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def dump_config(config: dict[str, Any]) -> str:
    return json.dumps(config, indent=2, sort_keys=True, default=str)
