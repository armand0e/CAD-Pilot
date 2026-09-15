"""CPU-only real-processor and loss-mask audit on the shortest train window from each app."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cad_policy.config import load_config
from cad_policy.data import PolicyDataset, dump_json
from cad_policy.model import load_processor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = {}
    for line in (args.view / "train.jsonl").open():
        row = json.loads(line)
        app = row["software"]
        if app not in selected or len(row["steps"]) < len(selected[app]["steps"]):
            selected[app] = row
    args.output.mkdir(parents=True, exist_ok=True)
    sample_path = args.output / "processor-sample.jsonl"
    with sample_path.open("w") as handle:
        for app, row in sorted(selected.items()):
            row["images"] = [str((args.view / path).resolve()) for path in row["images"]]
            handle.write(json.dumps(row) + "\n")
    config = load_config(args.config)
    processor = load_processor(config["model"])
    data = config["data"]
    dataset = PolicyDataset(sample_path, processor, max_total_tokens=data["max_total_tokens"],
                            min_pixels=data["image"]["min_pixels"], max_pixels=data["image"]["max_pixels"])
    results = []
    image_id = processor.tokenizer.convert_tokens_to_ids("<|image_pad|>")
    for i, row in enumerate(dataset.records):
        encoded = dataset.encode(i)
        ids, labels = encoded["input_ids"], encoded["labels"]
        supervised = labels != -100
        masked_text = processor.tokenizer.decode(ids[supervised])
        expected = "".join(step["completion"] + "<|im_end|>" for step in row["steps"][:dataset.plans[i]["steps"]])
        result = {"id": row["id"], "software": row["software"], "steps": encoded["supervised_spans"],
                  "tokens": ids.numel(), "planned_tokens": dataset.plans[i]["estimated_total_tokens"],
                  "supervised_tokens": supervised.sum().item(),
                  "image_tokens_supervised": ((ids == image_id) & supervised).sum().item(),
                  "only_exact_completions_supervised": masked_text == expected}
        assert result["only_exact_completions_supervised"] and result["image_tokens_supervised"] == 0, result
        results.append(result)
        print(json.dumps(result), flush=True)
        del encoded, ids, labels, supervised
    dump_json(args.output / "processor.json", {"sampling": "Shortest train window per app; not a random quality estimate", "results": results})


if __name__ == "__main__":
    main()
