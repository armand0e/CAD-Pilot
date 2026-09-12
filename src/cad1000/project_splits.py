"""Freeze deterministic project-family splits using shared native CAD artifact identities."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

CAD_SUFFIXES = (".dwg", ".dxf", ".sldprt", ".sldasm", ".catpart", ".catproduct", ".prt",
                ".rvt", ".skp", ".std", ".step", ".stp", ".iges", ".ipt", ".iam", ".3dm")


def project_split_plan(manifests: list[dict], seed: str = "cad-v3-split-2026-09-08") -> dict:
    keys = sorted(f"{m['software']}/{m['workflow_id']}" for m in manifests)
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate workflow identity")
    parent = {k: k for k in keys}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    artifacts = defaultdict(set)
    for manifest in manifests:
        k = f"{manifest['software']}/{manifest['workflow_id']}"
        for source in manifest.get("source_files", []):
            if source.get("oid") and source["name"].lower().endswith(CAD_SUFFIXES):
                artifacts[source["oid"]].add(k)
    for group in artifacts.values():
        roots = sorted({root(k) for k in group})
        for r in roots[1:]:
            parent[r] = roots[0]
    groups = defaultdict(list)
    for key in keys:
        groups[root(key)].append(key)
    group_apps = {g: {k.split("/")[0] for k in members} for g, members in groups.items()}
    apps = sorted({k.split("/")[0] for k in keys})
    assigned = {g: "train" for g in groups}
    targets = {}
    for app in apps:
        n = sum(app in a for a in group_apps.values())
        targets[app] = {"validation": max(1, round(n * .1)) if n >= 2 else 0,
                        "test": max(1, round(n * .1)) if n >= 3 else 0}
    # Small applications get their independent groups reserved first. Never remove the last
    # training family of an app while allocating a cross-application component.
    for app in sorted(apps, key=lambda a: (sum(a in x for x in group_apps.values()), a)):
        candidates = sorted((g for g in groups if app in group_apps[g]),
                            key=lambda g: hashlib.sha256(f"{seed}/{g}".encode()).hexdigest())
        for split in ("validation", "test"):
            for group in candidates:
                if sum(assigned[g] == split for g in candidates) >= targets[app][split]:
                    break
                if assigned[group] != "train":
                    continue
                if any(sum(assigned[g] == "train" and a in group_apps[g] for g in groups) <= 1 for a in group_apps[group]):
                    continue
                assigned[group] = split
    assignments = {k: {"split": assigned[root(k)], "project_group": root(k)} for k in keys}
    per_app = {a: dict(Counter(assignments[k]["split"] for k in keys if k.startswith(a + "/"))) for a in apps}
    limitations = {a: [s for s in ("validation", "test") if not per_app[a].get(s)] for a in apps}
    return {"version": "project-split-v3.1", "seed": seed, "assignments": assignments,
            "groups": dict(groups), "shared_artifacts": {oid: sorted(g) for oid, g in artifacts.items() if len(g) > 1},
            "per_app": per_app, "coverage_limitations": {a: s for a, s in limitations.items() if s},
            "source_revisions": sorted({m["revision"] for m in manifests})}
