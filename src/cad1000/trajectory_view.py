"""Trajectory training view: long windows of consecutive steps with screenshots and a text log.

Each ``cad-trajectory-1.0`` record is one window of a workflow: a text log of every accepted step
before the window, then ``n`` consecutive steps each carrying its pre-action screenshot and its
strict-JSON action target. The trainer supervises every action in the window, so one long
sequence teaches dozens of steps with full session history in context. The chat layout here is
the contract the runtime must reproduce (older steps as text, recent steps with images).
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .imagetokens import image_token_count, jpeg_size
from .policy_view import SYSTEM_PROMPT, convert_record, iter_policy_rows, validate_policy_actions
from .prepare import iter_json_lines

TRAJECTORY_SCHEMA_VERSION = "cad-trajectory-1.0"
SYSTEM_PROMPT_HISTORY = (
    SYSTEM_PROMPT
    + " This is a long session: earlier steps are listed as text (intent -> executed action); the most recent"
    " steps include their screenshots. Continue the session by returning the next action for the latest"
    " screenshot."
)
# Conservative characters-per-token for planning; the trainer re-measures with the real tokenizer.
CHARS_PER_TOKEN = 2.5
HEADER_TOKENS = 220


def _estimate_text_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def task_header(task: dict[str, Any], *, include_requirements: bool = True, include_description: bool = True) -> str:
    lines = [f"Application: {task.get('application')}"]
    if task.get("task"):
        lines.append(f"Task: {task['task']}")
    if include_description and task.get("description"):
        lines.append(f"Description: {task['description']}")
    if include_requirements and task.get("requirements"):
        lines.append("Requirements:")
        lines.extend(f"- {item}" for item in task["requirements"])
    return "\n".join(lines)


def log_line(number: int, step: dict[str, Any]) -> str:
    return f"{number}. {step.get('intent') or ''} -> {step['completion']}"


def step_text(number: int, step: dict[str, Any]) -> str:
    return f"Step {number}\nCurrent intent: {step.get('intent') or ''}\nReturn the next action(s) for the current screenshot as JSON."


def build_trajectory_messages(record: dict[str, Any], *, include_requirements: bool = True, include_description: bool = True) -> list[dict[str, Any]]:
    """Full multi-turn conversation (system, then user/assistant per step); images are placeholders."""
    steps = record["steps"]
    first_number = int(record["first_step_number"])
    header = task_header(record["task"], include_requirements=include_requirements, include_description=include_description)
    earlier = record.get("history_lines") or []
    omitted = int(record.get("omitted_history_steps") or 0)
    parts = [header, ""]
    if earlier or omitted:
        parts.append("Earlier steps:")
        if omitted:
            parts.append(f"({omitted} earlier steps omitted)")
        parts.extend(earlier)
    else:
        parts.append("Earlier steps: (none)")
    parts.append("")
    parts.append(step_text(first_number, steps[0]))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT_HISTORY},
        {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": "\n".join(parts)}]},
        {"role": "assistant", "content": steps[0]["completion"]},
    ]
    for offset, step in enumerate(steps[1:], start=1):
        messages.append({"role": "user", "content": [{"type": "image"}, {"type": "text", "text": step_text(first_number + offset, step)}]})
        messages.append({"role": "assistant", "content": step["completion"]})
    return messages


def _policy_step(row: dict[str, Any], *, max_chunk: int, max_gap_s: float, drag_mode: str) -> tuple[dict[str, Any] | None, str | None]:
    record, reason = convert_record(row, max_chunk=max_chunk, max_gap_s=max_gap_s, drag_mode=drag_mode, image_path=row["images"][0] if row.get("images") else None)
    if record is None:
        return None, reason
    return {
        "id": record["id"],
        "segment_index": record["source"]["segment_index"],
        "intent": record["intent"],
        "actions": record["actions"],
        "completion": record["completion"],
        "source_actions": record["source_actions"],
        "image": record["image"],
        "screen": record["screen"],
        "task": record["task"],
        "application": record["application"],
        "platform": record["source"]["platform"],
    }, None


def build_windows(
    steps: list[dict[str, Any]],
    *,
    budget_tokens: int,
    image_tokens: list[int],
    history_fraction: float = 0.2,
    fill_fraction: float = 0.92,
    max_windows: int | None = None,
    min_steps: int = 1,
) -> list[dict[str, Any]]:
    """Cut consecutive, non-overlapping windows that fit ``budget_tokens`` (estimated)."""
    windows: list[dict[str, Any]] = []
    start = 0
    log_lines = [log_line(i + 1, step) for i, step in enumerate(steps)]
    log_costs = [_estimate_text_tokens(line) + 1 for line in log_lines]
    while start < len(steps):
        history_budget = int(budget_tokens * history_fraction)
        # Keep the most recent log lines that fit the history budget.
        kept: list[str] = []
        used = 0
        for index in range(start - 1, -1, -1):
            if used + log_costs[index] > history_budget:
                break
            kept.append(log_lines[index])
            used += log_costs[index]
        kept.reverse()
        omitted = start - len(kept)
        total = HEADER_TOKENS + used + (12 if omitted else 0)
        end = start
        while end < len(steps):
            cost = image_tokens[end] + _estimate_text_tokens(step_text(end + 1, steps[end])) + _estimate_text_tokens(steps[end]["completion"]) + 24
            if end > start and total + cost > budget_tokens * fill_fraction:
                break
            total += cost
            end += 1
        if end - start >= min_steps:
            windows.append({"start": start, "end": end, "history_lines": kept, "omitted": omitted, "estimated_tokens": total})
        start = end
        if max_windows is not None and len(windows) >= max_windows:
            break
    return windows


def convert_trajectories(
    input_path: Path,
    output_dir: Path,
    *,
    budget_tokens: int = 65536,
    max_chunk: int = 1,
    max_gap_s: float = 1.0,
    drag_mode: str = "exclude",
    history_fraction: float = 0.2,
    max_windows_per_workflow: int | None = None,
    min_pixels: int = 65536,
    max_pixels: int = 1048576,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_root = input_path.parent
    by_workflow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    rejected: Counter[str] = Counter()
    for row in iter_json_lines(input_path):
        key = f"{row['source']['software']}/{row['source']['workflow_id']}"
        if "episode_id" in row["source"]:
            key += f"/episode-{row['source']['episode_id']:06d}"
        step, reason = _policy_step(row, max_chunk=max_chunk, max_gap_s=max_gap_s, drag_mode=drag_mode)
        if step is None:
            rejected[reason.split(":", 1)[0] if reason else "unknown"] += 1
            continue
        absolute = (frames_root / step["image"]).resolve()
        if not absolute.exists():
            rejected["missing_image_file"] += 1
            continue
        step["image"] = os.path.relpath(absolute, output_dir.resolve())
        step["image_tokens"] = image_token_count(*jpeg_size(absolute), min_pixels=min_pixels, max_pixels=max_pixels)
        by_workflow[key].append(step)
        meta.setdefault(key, {"split": row["split"], "software": row["source"]["software"], "workflow_id": row["source"]["workflow_id"], "platform": row["source"].get("platform"), "revision": row["source"].get("revision"), "episode_id": row["source"].get("episode_id")})
    rows: list[dict[str, Any]] = []
    for key, steps in by_workflow.items():
        steps.sort(key=lambda s: s["segment_index"])
        info = meta[key]
        windows = build_windows(steps, budget_tokens=budget_tokens, image_tokens=[s["image_tokens"] for s in steps], history_fraction=history_fraction, max_windows=max_windows_per_workflow)
        for number, window in enumerate(windows):
            window_steps = steps[window["start"] : window["end"]]
            rows.append(
                {
                    "schema_version": TRAJECTORY_SCHEMA_VERSION,
                    "id": f"{info['software']}--{info['workflow_id']}" + (f"--e{info['episode_id']:06d}" if info["episode_id"] is not None else "") + f"--w{number:04d}",
                    "split": info["split"],
                    "source": {"software": info["software"], "workflow_id": info["workflow_id"], "platform": info["platform"], "revision": info["revision"]},
                    "software": info["software"],
                    "application": window_steps[0]["application"],
                    "screen": window_steps[0]["screen"],
                    "task": window_steps[0]["task"],
                    "first_step_number": window["start"] + 1,
                    "total_steps": len(steps),
                    "history_lines": window["history_lines"],
                    "omitted_history_steps": window["omitted"],
                    "images": [s["image"] for s in window_steps],
                    "steps": [{k: s[k] for k in ("id", "intent", "actions", "completion", "source_actions")} for s in window_steps],
                    "estimated_tokens": window["estimated_tokens"],
                }
            )
    for name in ("all", "train", "validation", "test"):
        selected = rows if name == "all" else [r for r in rows if r["split"] == name]
        with (output_dir / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in selected:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    report = trajectory_statistics(rows) | {
        "rejected_reasons": dict(rejected.most_common()),
        "parameters": {"budget_tokens": budget_tokens, "max_chunk": max_chunk, "max_gap_s": max_gap_s, "drag_mode": drag_mode, "history_fraction": history_fraction, "max_windows_per_workflow": max_windows_per_workflow, "input": str(input_path)},
    }
    with (output_dir / "report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def trajectory_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [len(r["steps"]) for r in rows]
    est = sorted(r["estimated_tokens"] for r in rows)
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "window_count": len(rows),
        "supervised_steps": sum(steps),
        "workflow_count": len({f"{r['software']}/{r['source']['workflow_id']}" for r in rows}),
        "split_counts": dict(Counter(r["split"] for r in rows)),
        "software_counts": dict(Counter(r["software"] for r in rows).most_common()),
        "steps_per_window": {"min": min(steps, default=0), "mean": round(sum(steps) / len(steps), 1) if steps else 0, "max": max(steps, default=0)},
        "estimated_tokens": {"min": est[0] if est else 0, "p50": est[len(est) // 2] if est else 0, "max": est[-1] if est else 0},
        "windows_with_omitted_history": sum(1 for r in rows if r["omitted_history_steps"]),
    }


def validate_trajectory_records(paths: Iterable[Path]) -> dict[str, Any]:
    count = 0
    workflows_by_split: dict[str, set[str]] = {n: set() for n in ("train", "validation", "test")}
    errors: list[str] = []
    for path in paths:
        for row in iter_policy_rows(path):
            count += 1
            row_id = row.get("id", f"{path}:{count}")
            if row.get("split") not in workflows_by_split:
                errors.append(f"{row_id}: invalid split")
                continue
            workflows_by_split[row["split"]].add(f"{row['software']}/{row['source']['workflow_id']}")
            if len(row.get("images", [])) != len(row.get("steps", [])) or not row.get("steps"):
                errors.append(f"{row_id}: images/steps mismatch")
                continue
            for step in row["steps"]:
                if validate_policy_actions(step["actions"]) or json.dumps({"actions": step["actions"]}, ensure_ascii=False, separators=(",", ":")) != step["completion"]:
                    errors.append(f"{row_id}: bad step {step.get('id')}")
                    break
            for image in row["images"]:
                if not (path.parent / image).exists():
                    errors.append(f"{row_id}: missing image {image}")
                    break
            first = int(row.get("first_step_number") or 0)
            lines = row.get("history_lines") or []
            omitted = int(row.get("omitted_history_steps") or 0)
            numbers = [int(line.split(".", 1)[0]) for line in lines if line.split(".", 1)[0].isdigit()]
            if len(numbers) != len(lines) or len(lines) + omitted != first - 1 or any(n >= first for n in numbers):
                errors.append(f"{row_id}: history log numbering does not precede the window")
    names = list(workflows_by_split)
    leakage: set[str] = set()
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            leakage.update(workflows_by_split[left] & workflows_by_split[right])
    if leakage:
        errors.append(f"workflow leakage across splits: {sorted(leakage)}")
    if errors:
        raise ValueError("Trajectory validation failed:\n" + "\n".join(errors[:50]))
    return {"record_count": count, "workflow_counts": {n: len(v) for n, v in workflows_by_split.items()}, "errors": 0}
