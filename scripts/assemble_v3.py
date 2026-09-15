"""Stream prevalidated per-workflow trajectory views into a new, immutable training view."""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cad1000.shards import completed_shards, sha256_file, write_json_atomic
from cad1000.trajectory_view import validate_trajectory_records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output exists: immutable training views are never overwritten")
    args.output.mkdir(parents=True)
    handles = {n: (args.output / f"{n}.jsonl").open("w") for n in ("all", "train", "validation", "test")}
    counts, steps, apps = Counter(), Counter(), Counter()
    for folder, manifest in completed_shards(args.shards):
        source = folder / "trajectory/all.jsonl"
        for line in source.open():
            row = json.loads(line)
            row["images"] = [os.path.relpath((source.parent / p).resolve(), args.output.resolve()) for p in row["images"]]
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            handles["all"].write(encoded)
            handles[row["split"]].write(encoded)
            counts[row["split"]] += 1
            steps[row["split"]] += len(row["steps"])
            apps[row["software"]] += 1
    for handle in handles.values():
        handle.close()
    validation = validate_trajectory_records([args.output / "all.jsonl"])
    write_json_atomic(args.output / "report.json", {"schema_version": "cad-trajectory-1.0", "recipe": "v3",
        "split_counts": counts, "supervised_steps": steps, "software_counts": apps, "validation": validation,
        "build": json.loads((args.shards / "build.json").read_text()),
        "checksums": {n: sha256_file(args.output / f"{n}.jsonl") for n in handles}})


if __name__ == "__main__":
    main()
