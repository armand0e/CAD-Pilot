"""Reproducibility manifest written next to every run, preflight and evaluation."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import REPO_ROOT

TRACKED_SOURCE_GLOBS = ("src/cad1000/*.py", "training/cad_policy/*.py", "training/*.py", "training/configs/*.yaml")
PACKAGES = ("torch", "transformers", "trl", "peft", "datasets", "bitsandbytes", "accelerate", "PIL", "fla", "causal_conv1d", "yaml")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": sys.version.split()[0]}
    for name in PACKAGES:
        try:
            module = importlib.import_module(name)
            versions[name] = getattr(module, "__version__", "installed")
        except Exception:  # noqa: BLE001
            versions[name] = None
    return versions


def source_fingerprint() -> dict[str, Any]:
    """Git commit/diff when available, otherwise sha256 of the tracked source files."""
    info: dict[str, Any] = {"repo_root": str(REPO_ROOT)}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        info["git_commit"] = head.stdout.strip()
        diff = subprocess.run(["git", "diff", "HEAD", "--stat"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        info["git_diff_stat"] = diff.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        info["git_commit"] = None
        info["note"] = "not a git repository; file hashes recorded instead"
    files: dict[str, str] = {}
    for pattern in TRACKED_SOURCE_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            files[str(path.relative_to(REPO_ROOT))] = sha256_file(path)
    info["source_sha256"] = files
    return info


def gpu_info() -> dict[str, Any]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"available": False}
        properties = torch.cuda.get_device_properties(0)
        return {
            "available": True,
            "name": properties.name,
            "total_gib": round(properties.total_memory / 2**30, 2),
            "capability": f"{properties.major}.{properties.minor}",
            "cuda": torch.version.cuda,
        }
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": repr(error)}


def data_fingerprint(policy_dir: Path, files: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"policy_dir": str(policy_dir)}
    for name in files:
        path = policy_dir / name
        result[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size} if path.exists() else None
    report = policy_dir / "report.json"
    if report.exists():
        result["report"] = json.loads(report.read_text(encoding="utf-8"))
    return result


def build_manifest(kind: str, config: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "created_unix": int(time.time()),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": platform.node(),
        "cwd": os.getcwd(),
        "argv": sys.argv,
        "config": config,
        "packages": package_versions(),
        "source": source_fingerprint(),
        "gpu": gpu_info(),
        **extra,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)
