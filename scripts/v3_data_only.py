"""Durable, restartable 597-workflow rebuild. This chain has NO training stage.

Run in a resource-bounded systemd user service. Outputs are immutable; completed
shards survive retries. Only the dedicated, redownloadable source cache is pruned.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from cad1000.shards import completed_shards, sha256_file, write_json_atomic
from rebuild_v3 import fingerprint

RUN = ROOT / "runs/v3-data-only-20260908"
SHARDS = ROOT / "data/shards-v3"
CACHE = ROOT / "data/raw-v3-full-cache"
VIEW = ROOT / "data/processed/trajectory-v3"
HOLD = ROOT / "runs/full-run/quality-hold.md"
SPLITS = ROOT / "data/splits-v3.json"
PYTHON = ROOT / "harness/.venv/bin/python"
PROCESSOR_PYTHON = Path("/home/armand0e/Documents/training/.venv/bin/python")


def check_gate(gate):
    if not HOLD.is_file() or not (RUN / "training-hold.md").is_file():
        raise ValueError("Training hold missing; data-only chain stopped")
    if gate["status"] != "passed_for_rebuild" or gate["training_authorized"] is not False:
        raise ValueError("Sample gate does not authorize a data-only rebuild")
    if gate["build"]["code_sha256"] != fingerprint() or gate["build"]["split_sha256"] != sha256_file(SPLITS):
        raise ValueError("Builder or split recipe changed after the sample gate")
    for path, digest in gate["reports"].items():
        if sha256_file(ROOT / path) != digest:
            raise ValueError(f"Sample gate evidence changed: {path}")


def status(phase, **extra):
    result = {"phase": phase, "updated_at": datetime.now(timezone.utc).isoformat(),
              "training_started": False, "training_authorized": False, **extra}
    write_json_atomic(RUN / "status.json", result)
    print(json.dumps(result), flush=True)


def worker_command(index, workers):
    return [str(PYTHON), "scripts/rebuild_v3.py", "--output", str(SHARDS), "--cache", str(CACHE),
            "--splits", str(SPLITS), "--workers", str(workers), "--worker-index", str(index),
            "--min-free-gib", "80", "--cleanup"]  # Deliberately NO sample or action cap.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / "chain.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gate = json.loads(args.gate.read_text())
        check_gate(gate)
        if args.check_only:
            print("Gate accepted; build-only commands:")
            print(json.dumps([worker_command(i, args.workers) for i in range(args.workers)], indent=2))
            return
        env = os.environ | {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false"}
        active, handles, attempts, finished = {}, [], {}, set()

        def stop(_sig, _frame):
            raise InterruptedError("Data-only service interrupted; completed outputs preserved")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def command(name, argv, *, offline=False):
            check_gate(gate)
            status(name, completed_workflows=len(completed_shards(SHARDS)))
            handle = (RUN / f"{name}.log").open("a")
            handles.append(handle)
            proc = subprocess.Popen(argv, cwd=ROOT, env=env | ({"HF_HUB_OFFLINE": "1"} if offline else {}),
                                    stdout=handle, stderr=subprocess.STDOUT)
            active[name] = proc
            code = proc.wait()
            active.pop(name)
            if code:
                raise RuntimeError(f"{name} failed with exit {code}; see {RUN / (name + '.log')}")

        try:
            while len(finished) < args.workers:
                check_gate(gate)
                for index in range(args.workers):
                    if index in finished:
                        continue
                    if index not in active:
                        attempts[index] = attempts.get(index, 0) + 1
                        handle = (RUN / f"worker-{index}-attempt-{attempts[index]}.log").open("a")
                        handles.append(handle)
                        active[index] = subprocess.Popen(worker_command(index, args.workers), cwd=ROOT, env=env,
                                                         stdout=handle, stderr=subprocess.STDOUT)
                    code = active[index].poll()
                    if code is not None:
                        active.pop(index)
                        if code == 0:
                            finished.add(index)
                        elif attempts[index] >= 3:
                            raise RuntimeError(f"Worker {index} failed three attempts; completed shards preserved")
                status("rebuilding", completed_workflows=len(completed_shards(SHARDS)), expected_workflows=597,
                       worker_pids={str(k): p.pid for k, p in active.items()}, attempts=attempts)
                if len(finished) < args.workers:
                    time.sleep(30)
            command("final-structural", [str(PYTHON), "scripts/audit_v3.py", "--shards", str(SHARDS),
                                         "--expected", "597", "--output", str(RUN / "final-structural.json")])
            command("final-processor", [str(PROCESSOR_PYTHON), "training/audit_v3_data.py", "--config",
                                        "training/configs/v3.yaml", "--shards", str(SHARDS), "--expected", "597",
                                        "--output", str(RUN / "final-processor")], offline=True)
            if not VIEW.exists():
                staging = VIEW.with_name(VIEW.name + f".building-{time.time_ns()}")
                command("assemble", [str(PYTHON), "scripts/assemble_v3.py", "--shards", str(SHARDS), "--output", str(staging)])
                staging.rename(VIEW)
            report = json.loads((VIEW / "report.json").read_text())
            if report["build"] != json.loads((SHARDS / "build.json").read_text()):
                raise ValueError("Assembled view has a different recipe")
            for name, digest in report["checksums"].items():
                if sha256_file(VIEW / f"{name}.jsonl") != digest:
                    raise ValueError(f"Assembled view changed: {name}")
            audit = json.loads((RUN / "final-structural.json").read_text())
            if audit["status"] != "passed" or report["supervised_steps"] != audit["split_counts"]:
                raise ValueError("Final assembly coverage mismatch")
            check_gate(gate)
            completion = {"status": "complete_stopped_before_training", "training_started": False,
                          "training_authorized": False, "workflows": 597, "actions": audit["rows"],
                          "view": str(VIEW), "split_steps": report["supervised_steps"], "windows": report["split_counts"],
                          "build": report["build"], "checksums": report["checksums"],
                          "pre_training_limitations": gate["pre_training_limitations"]}
            write_json_atomic(RUN / "data-ready.json", completion)
            status("complete_stopped_before_training", completed_workflows=597, actions=audit["rows"])
        except BaseException as error:
            status("failed_stopped_before_training", error=str(error), completed_workflows=len(completed_shards(SHARDS)))
            raise
        finally:
            for proc in active.values():
                if proc.poll() is None:
                    proc.terminate()
            for proc in active.values():
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for handle in handles:
                handle.close()


if __name__ == "__main__":
    main()
