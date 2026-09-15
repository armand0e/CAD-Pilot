#!/usr/bin/env python
"""Merge a LoRA adapter into BF16 base weights for serving (or serve the adapter directly).

    python training/merge_adapter.py --config training/configs/lora_bf16.yaml --adapter runs/lora-bf16/adapter --output runs/lora-bf16/merged

Serving options afterwards:
  * merged weights:  vllm serve runs/lora-bf16/merged --served-model-name cad-policy \
                       --default-chat-template-kwargs '{"enable_thinking": false}'
  * adapter only:    vllm serve Qwen/Qwen3.5-9B --enable-lora --lora-modules cad-policy=runs/lora-bf16/adapter \
                       --max-lora-rank 32 --default-chat-template-kwargs '{"enable_thinking": false}'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cad_policy.config import load_config, resolve_path  # noqa: E402
from cad_policy.manifest import build_manifest, write_manifest  # noqa: E402
from cad_policy.model import load_base_model, load_processor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cpu", help="cpu (default, needs ~20 GiB RAM+swap) or cuda")
    args = parser.parse_args()
    config = load_config(args.config)
    if config["model"].get("quantization", "none") != "none":
        raise SystemExit("merge from the bf16 base, not a quantized one: set model.quantization: none")
    from peft import PeftModel

    device_map = {"": 0} if args.device == "cuda" else "cpu"
    model = load_base_model(config["model"], device_map=device_map, training=False)
    model = PeftModel.from_pretrained(model, args.adapter)
    merged = model.merge_and_unload()
    output = resolve_path(args.output)
    merged.save_pretrained(str(output), safe_serialization=True, max_shard_size="5GB")
    load_processor(config["model"]).save_pretrained(str(output))
    write_manifest(output / "merge_manifest.json", build_manifest("merge", config, adapter=args.adapter))
    print(f"merged weights written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
