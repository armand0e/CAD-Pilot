#!/usr/bin/env python
"""Dataset statistics and token/image-token length report for a policy-view directory.

    /home/armand0e/Documents/training/.venv/bin/python training/token_report.py --config training/configs/lora_bf16.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cad_policy.config import load_config, resolve_path  # noqa: E402
from cad_policy.data import dump_json, load_policy_dataset  # noqa: E402
from cad_policy.model import load_processor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--splits", nargs="+", default=["train.jsonl", "validation.jsonl", "test.jsonl"])
    parser.add_argument("--output", default=None)
    parser.add_argument("--policy-dir", default=None, help="Override data.policy_dir")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.policy_dir:
        config["data"]["policy_dir"] = args.policy_dir
    processor = load_processor(config["model"])
    policy_dir = resolve_path(config["data"]["policy_dir"])
    report = {"policy_dir": str(policy_dir), "max_total_tokens": config["data"]["max_total_tokens"], "splits": {}}
    for split in args.splits:
        path = policy_dir / split
        if not path.exists() or path.stat().st_size == 0:
            report["splits"][split] = None
            continue
        dataset = load_policy_dataset(config, processor, split, max_examples=None)
        report["splits"][split] = dataset.length_report() | {"excluded_ids": dataset.excluded[:100]}
    output = Path(args.output) if args.output else policy_dir / "token_report.json"
    dump_json(output, report)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
