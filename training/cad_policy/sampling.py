"""Deterministic coverage and app/project-stratified subset selection."""
from __future__ import annotations

import random
from collections import defaultdict, deque


def stratified_indices(rows, limit, seed):
    if limit is None or limit >= len(rows):
        return list(range(len(rows)))
    groups = defaultdict(lambda: defaultdict(list))
    for i, row in enumerate(rows):
        groups[row["software"]][row.get("source", {}).get("workflow_id", row["id"])].append(i)
    if limit < len(groups):
        raise ValueError(f"subset limit {limit} cannot cover {len(groups)} applications")
    rng = random.Random(seed)
    queues = {}
    for app, workflows in sorted(groups.items()):
        families = list(sorted(workflows))
        rng.shuffle(families)
        for values in workflows.values():
            rng.shuffle(values)
        ordered = []
        while any(workflows.values()):
            for family in families:
                if workflows[family]:
                    ordered.append(workflows[family].pop())
        queues[app] = deque(ordered)
    keep = []
    while len(keep) < limit:
        for app in sorted(queues):
            if queues[app] and len(keep) < limit:
                keep.append(queues[app].popleft())
    return sorted(keep)
