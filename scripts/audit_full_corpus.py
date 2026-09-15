"""Refuse to call a partial corpus the full run. No GPU dependencies."""
import json
from collections import Counter
from pathlib import Path

from cad1000.hf import DEFAULT_REPO, DEFAULT_REVISION
from cad1000.shards import ALL_SOFTWARE, plan_workflows


def main():
    planned = plan_workflows(repo=DEFAULT_REPO, revision=DEFAULT_REVISION,
                             softwares=ALL_SOFTWARE, workflow_ids=None, max_per_software=None)
    statuses = Counter()
    failures = []
    for workflow in planned:
        path = Path("data/shards") / workflow / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        status = manifest.get("status", "missing")
        statuses[status] += 1
        if status not in {"complete", "skipped"}:
            failures.append(workflow)
    print(json.dumps({"planned": len(planned), "statuses": statuses, "incomplete": failures}, indent=2))
    return int(len(planned) != 597 or bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
