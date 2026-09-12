"""Convert raw CAD workflows into compact VLA/SFT training records."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "cad-vla-1.1"
ACTION_TYPES = {"click", "drag", "scroll", "key"}
IDLE_TERMS = (
    "waited",
    "remained idle",
    "paused",
    "no interaction",
    "no modeling interaction",
)
# Below this on-screen displacement a press/drag/release gesture is treated as a plain click.
MIN_DRAG_DISTANCE_PX = 4.0
# Consecutive printable key presses closer than this are one typed value (e.g. "40").
TYPING_GAP_S = 0.75

APP_ALIASES = {
    "autocad": ("autocad",),
    "catia": ("catia",),
    "nx-cad": ("siemensnx", "nxcad", "nx"),
    "p-d5": ("d5render", "d5"),
    "revit-architecture": ("revit",),
    "revit-structure": ("revit",),
    "sketchup": ("sketchup",),
    "solidworks": ("solidworks",),
    "staad-pro": ("staadpro", "staad"),
    "vray": ("vray",),
}


def _compact(text: str | None) -> str:
    return "".join(character.lower() for character in (text or "") if character.isalnum())


def app_matches(value: str | None, software: str, application: str) -> bool:
    compact = _compact(value)
    if not compact:
        return False
    aliases = set(APP_ALIASES.get(software, ()))
    aliases.add(_compact(application))
    return any(alias and (alias in compact or compact in alias) for alias in aliases)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"Expected an object in {path}")
    return value


def iter_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {error}") from error
            if isinstance(value, dict):
                yield value


def workflow_split(workflow_key: str, train_ratio: float, validation_ratio: float) -> str:
    bucket = int(hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()[:8], 16) / 2**32
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + validation_ratio:
        return "validation"
    return "test"


def _finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


@dataclass
class EventIndex:
    actions: list[dict[str, Any]]
    action_times: list[float]
    privacy_intervals: list[tuple[float, float]]
    screen_width: int
    screen_height: int
    raw_event_count: int
    event_type_counts: Counter[str]
    drag_stats: Counter[str] = field(default_factory=Counter)
    typing_groups: list[tuple[int, int]] = field(default_factory=list)
    group_of: dict[int, int] = field(default_factory=dict)

    def actions_between(self, start_s: float, end_s: float) -> list[dict[str, Any]]:
        """Actions in ``[start_s, end_s)``, never splitting a typed value across the boundary.

        A typing group (consecutive printable key presses <= ``TYPING_GAP_S`` apart) belongs to
        the span in which it started: leading actions from a group that began earlier are dropped,
        and a group that continues past ``end_s`` is pulled in whole.
        """
        left = bisect.bisect_left(self.action_times, start_s)
        right = bisect.bisect_left(self.action_times, end_s)
        while left < right and left in self.group_of and self.typing_groups[self.group_of[left]][0] < left:
            left += 1
        if right > left and (right - 1) in self.group_of:
            right = max(right, self.typing_groups[self.group_of[right - 1]][1] + 1)
        return self.actions[left:right]

    def overlaps_privacy(self, start_s: float, end_s: float) -> bool:
        return any(
            interval_start < end_s and interval_end > start_s
            for interval_start, interval_end in self.privacy_intervals
        )


def _normalized(value: float, extent: int) -> float:
    return round(min(1.0, max(0.0, value / extent)), 6)


def load_event_index(path: Path, *, software: str, application: str) -> EventIndex:
    """Filter the raw event stream to executable, target-application policy actions.

    Drags are reconstructed from ``click(isDown) -> drag* -> click(isUp)`` gestures: the raw
    ``drag`` events are only intermediate pointer samples, so a lone drag sample never becomes an
    action. A gesture whose displacement is below ``MIN_DRAG_DISTANCE_PX`` stays a ``click``.
    """
    actions: list[dict[str, Any]] = []
    privacy_intervals: list[tuple[float, float]] = []
    privacy_start: float | None = None
    first_timestamp_ms: float | None = None
    width, height = 1920, 1080
    raw_count = 0
    counts: Counter[str] = Counter()
    drag_stats: Counter[str] = Counter()
    # Open press gesture: (index into ``actions`` of the click-down, raw pixel start, samples)
    pending: dict[str, Any] | None = None

    def close_gesture(up_event: dict[str, Any] | None, time_s: float | None) -> None:
        nonlocal pending
        if pending is None:
            return
        if pending["samples"]:
            end = pending["samples"][-1]
            if up_event is not None:
                end_x = _finite_number(up_event.get("x"))
                end_y = _finite_number(up_event.get("y"))
                if end_x is not None and end_y is not None:
                    end = (end_x, end_y)
            distance = math.hypot(end[0] - pending["start"][0], end[1] - pending["start"][1])
            if distance >= MIN_DRAG_DISTANCE_PX:
                action = actions[pending["index"]]
                action["type"] = "drag"
                action["end_x"] = _normalized(end[0], width)
                action["end_y"] = _normalized(end[1], height)
                action["path_points"] = len(pending["samples"])
                if time_s is not None:
                    action["duration"] = round(max(0.0, time_s - action["t"]), 3)
                drag_stats["drags"] += 1
            else:
                drag_stats["tiny_drags_as_click"] += 1
        pending = None

    for event in iter_json_lines(path):
        raw_count += 1
        event_type = str(event.get("type", "unknown"))
        counts[event_type] += 1
        timestamp = _finite_number(event.get("timestamp"))
        if timestamp is None:
            continue
        if first_timestamp_ms is None:
            first_timestamp_ms = timestamp
        video_t_ms = _finite_number(event.get("video_t_ms"))
        time_s = (
            (video_t_ms / 1000.0)
            if video_t_ms is not None
            else (timestamp - first_timestamp_ms) / 1000.0
        )

        if event_type == "screen_config":
            screens = event.get("screens") or []
            if screens:
                width = int(screens[0].get("width") or width)
                height = int(screens[0].get("height") or height)
            continue
        if event_type == "privacy_mask_start":
            privacy_start = time_s
            close_gesture(None, None)
            continue
        if event_type == "privacy_mask_end":
            if privacy_start is not None:
                privacy_intervals.append((privacy_start, max(privacy_start, time_s)))
                privacy_start = None
            continue
        if event_type not in ACTION_TYPES:
            continue
        if privacy_start is not None:
            continue

        if event_type == "drag":
            if pending is None:
                drag_stats["orphan_drag_samples"] += 1
                continue
            x = _finite_number(event.get("x"))
            y = _finite_number(event.get("y"))
            if x is not None and y is not None:
                pending["samples"].append((x, y))
            continue
        if event_type == "click" and not event.get("isDown", True):
            close_gesture(event, time_s)
            continue

        event_app = event.get("appName")
        if event_app and not app_matches(str(event_app), software, application):
            if event_type == "click":
                close_gesture(None, None)
            continue
        if event_type == "key" and not event.get("isDown", True):
            continue

        action: dict[str, Any] = {"type": event_type, "t": round(time_s, 3)}
        if event_type in {"click", "scroll"}:
            x = _finite_number(event.get("x"))
            y = _finite_number(event.get("y"))
            if x is not None and y is not None:
                action["x"] = _normalized(x, width)
                action["y"] = _normalized(y, height)
            if event.get("button"):
                action["button"] = str(event["button"])
        if event_type == "scroll":
            action["delta_x"] = event.get("deltaX", 0)
            action["delta_y"] = event.get("deltaY", 0)
        if event_type == "key":
            action["key_code"] = event.get("keyCode")
            action["text"] = str(event.get("characters") or "")
            action["modifiers"] = event.get("modifiers", 0)
        if event_type == "click":
            close_gesture(None, None)
            x = _finite_number(event.get("x"))
            y = _finite_number(event.get("y"))
            if x is not None and y is not None:
                pending = {"index": len(actions), "start": (x, y), "samples": []}
        actions.append(action)

    close_gesture(None, None)
    if privacy_start is not None:
        privacy_intervals.append((privacy_start, float("inf")))
    actions.sort(key=lambda item: item["t"])
    groups = typing_groups(actions)
    return EventIndex(
        actions=actions,
        action_times=[item["t"] for item in actions],
        privacy_intervals=privacy_intervals,
        screen_width=width,
        screen_height=height,
        raw_event_count=raw_count,
        event_type_counts=counts,
        drag_stats=drag_stats,
        typing_groups=groups,
        group_of={index: g for g, (first, last) in enumerate(groups) for index in range(first, last + 1)},
    )


def typing_groups(actions: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Index ranges of consecutive typing keys separated by <= TYPING_GAP_S (length >= 2)."""
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, action in enumerate(actions):
        if action["type"] == "key" and _is_typing_key(action):
            if start is not None and action["t"] - actions[index - 1]["t"] <= TYPING_GAP_S:
                continue
            if start is not None and index - 1 > start:
                groups.append((start, index - 1))
            start = index
        else:
            if start is not None and index - 1 > start:
                groups.append((start, index - 1))
            start = None
    if start is not None and len(actions) - 1 > start:
        groups.append((start, len(actions) - 1))
    return groups


def _is_typing_key(action: dict[str, Any]) -> bool:
    """Unmodified (or shift-only) printable key presses become typed text."""
    text = action.get("text")
    if not text or not str(text).isprintable():
        return False
    return int(action.get("modifiers") or 0) & ~1 == 0


def compact_actions(actions: list[dict[str, Any]], start_s: float) -> list[dict[str, Any]]:
    """Make actions target-sized while retaining CAD dimensions and shortcuts."""
    result: list[dict[str, Any]] = []
    text_buffer: dict[str, Any] | None = None
    for source in actions:
        action = dict(source)
        action["dt"] = round(action.pop("t") - start_s, 3)
        if action["type"] == "key" and _is_typing_key(action):
            if text_buffer is not None and action["dt"] - text_buffer["last_dt"] <= TYPING_GAP_S:
                text_buffer["text"] += action["text"]
                text_buffer["last_dt"] = action["dt"]
                continue
            text_buffer = {
                "type": "type_text",
                "text": action["text"],
                "dt": action["dt"],
                "last_dt": action["dt"],
            }
            result.append(text_buffer)
            continue
        text_buffer = None
        result.append(action)
    for item in result:
        item.pop("last_dt", None)
    return result


def quality_for(
    annotation: dict[str, Any],
    actions: list[dict[str, Any]],
    *,
    target_app: bool,
    privacy_overlap: bool,
    max_segment_s: float,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    duration = float(annotation["end_s"]) - float(annotation["start_s"])
    narrative = " ".join(
        str(annotation.get(field) or "") for field in ("action", "intent", "details")
    ).lower()
    if actions:
        score += 0.4
    else:
        reasons.append("no_policy_actions")
    if target_app:
        score += 0.2
    else:
        reasons.append("off_target_app")
    if not any(term in narrative for term in IDLE_TERMS):
        score += 0.2
    else:
        reasons.append("idle_or_waiting")
    if 0.25 <= duration <= max_segment_s:
        score += 0.1
    else:
        reasons.append("duration_out_of_range")
    if not privacy_overlap:
        score += 0.1
    else:
        reasons.append("privacy_mask_overlap")
    return round(score, 3), reasons


def _frame_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as error:
        raise RuntimeError(
            "Frame extraction requires ffmpeg or `pip install -e '.[frames]'`."
        ) from error


def extract_frame(video: Path, timestamp_s: float, destination: Path, ffmpeg: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "3",
        "-y",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for {video} at {timestamp_s}s: {completed.stderr.strip()}"
        )


def find_workflows(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/*/task_desc.json"))


def _chat_messages(
    task: dict[str, Any], annotation: dict[str, Any], actions: list[dict[str, Any]]
) -> list[dict[str, str]]:
    requirements = "\n".join(f"- {item}" for item in task["requirements"])
    user = (
        f"Application: {task['application']}\n"
        f"Task: {task['task']}\n"
        f"Description: {task['description']}\n"
        f"Requirements:\n{requirements}\n"
        f"Current intent: {annotation.get('intent') or annotation.get('action')}\n"
        "Given the current screenshot, return the next verified GUI actions."
    )
    assistant = json.dumps(
        {"summary": annotation.get("action"), "actions": actions},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        {
            "role": "system",
            "content": (
                "You are a professional CAD operator. Use normalized screen coordinates, preserve "
                "design intent, and execute only actions supported by the visible application state."
            ),
        },
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def load_source_manifest(input_root: Path) -> list[dict[str, Any]]:
    source_manifest_path = input_root / "source_manifest.json"
    if not source_manifest_path.exists():
        return []
    with source_manifest_path.open("r", encoding="utf-8") as handle:
        loaded_manifest = json.load(handle)
    return loaded_manifest if isinstance(loaded_manifest, list) else []


def prepare_dataset(
    input_root: Path,
    output_root: Path,
    *,
    min_quality: float = 0.9,
    max_segment_s: float = 30.0,
    max_actions: int = 128,
    observation_lead_s: float = 0.5,
    max_examples: int | None = None,
    train_ratio: float = 0.9,
    validation_ratio: float = 0.05,
    extract_frames: bool = False,
    workflows: Iterable[Path] | None = None,
) -> dict[str, Any]:
    if (
        not 0 < train_ratio < 1
        or not 0 <= validation_ratio < 1
        or train_ratio + validation_ratio >= 1
    ):
        raise ValueError("Split ratios must leave non-zero train and test partitions")
    if observation_lead_s < 0:
        raise ValueError("observation_lead_s must be non-negative")
    workflow_paths = sorted(workflows) if workflows is not None else find_workflows(input_root)
    if not workflow_paths:
        raise FileNotFoundError(f"No workflows found below {input_root}")

    output_root.mkdir(parents=True, exist_ok=True)
    ffmpeg = _frame_executable() if extract_frames else ""
    source_manifest = load_source_manifest(input_root)
    provenance = {
        f"{item.get('software')}/{item.get('workflow_id')}": item for item in source_manifest
    }
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    drag_stats: Counter[str] = Counter()
    raw_events = policy_events = 0

    for workflow in workflow_paths:
        software = workflow.parent.name
        workflow_id = workflow.name
        metadata = read_json(workflow / "metadata.json")
        task_source = read_json(workflow / "task_desc.json")
        rubric_source = read_json(workflow / "rubrics.json")
        narration = read_json(workflow / "narration.json")
        application = str(task_source.get("application") or software)
        event_index = load_event_index(
            workflow / "events.json", software=software, application=application
        )
        raw_events += event_index.raw_event_count
        policy_events += len(event_index.actions)
        event_counts.update(event_index.event_type_counts)
        drag_stats.update(event_index.drag_stats)
        workflow_key = f"{software}/{workflow_id}"
        workflow_provenance = provenance.get(workflow_key, {})
        split = workflow_split(workflow_key, train_ratio, validation_ratio)
        requirements = [
            str(item.get("requirement", "")).strip()
            for item in rubric_source.get("rubrics", [])
            if item.get("requirement")
        ]
        task = {
            "domain": task_source.get("domain"),
            "application": application,
            "task": task_source.get("task"),
            "description": task_source.get("description"),
            "deliverables": task_source.get("deliverables", []),
            "requirements": requirements,
        }

        for segment_number, annotation in enumerate(narration.get("annotations", [])):
            start_s = _finite_number(annotation.get("start_s"))
            end_s = _finite_number(annotation.get("end_s"))
            if start_s is None or end_s is None or end_s <= start_s:
                rejected["invalid_timestamps"] += 1
                continue
            segment_actions = event_index.actions_between(start_s, end_s)
            target_app = app_matches(str(annotation.get("app") or ""), software, application)
            privacy_overlap = event_index.overlaps_privacy(start_s, end_s)
            quality, reasons = quality_for(
                annotation,
                segment_actions,
                target_app=target_app,
                privacy_overlap=privacy_overlap,
                max_segment_s=max_segment_s,
            )
            if not target_app:
                rejected["off_target_app"] += 1
                continue
            if privacy_overlap:
                rejected["privacy_mask_overlap"] += 1
                continue
            if quality < min_quality:
                rejected.update(reasons or ["below_quality_threshold"])
                continue
            observation_s = max(start_s, float(segment_actions[0]["t"]) - observation_lead_s)
            compacted = compact_actions(segment_actions[:max_actions], observation_s)
            if not compacted:
                rejected["no_compacted_actions"] += 1
                continue

            example_id = f"{software}--{workflow_id}--{segment_number:05d}"
            observation: dict[str, Any] = {
                "video": str((workflow / "clip.mp4").relative_to(input_root)),
                "timestamp_s": round(observation_s, 3),
                "screen": {"width": event_index.screen_width, "height": event_index.screen_height},
            }
            images: list[str] = []
            if extract_frames:
                video = workflow / "clip.mp4"
                if not video.exists():
                    raise FileNotFoundError(f"Missing clip required for frames: {video}")
                frame_relative = Path("frames") / split / f"{example_id}.jpg"
                extract_frame(video, observation_s, output_root / frame_relative, ffmpeg)
                observation["image"] = str(frame_relative)
                images = [str(frame_relative)]

            row = {
                "schema_version": SCHEMA_VERSION,
                "id": example_id,
                "split": split,
                "source": {
                    "dataset": workflow_provenance.get("repo", "markov-ai/cad-1000-hours"),
                    "revision": workflow_provenance.get("revision"),
                    "software": software,
                    "workflow_id": workflow_id,
                    "segment_index": segment_number,
                    "platform": metadata.get("platform"),
                    "fps": metadata.get("fps"),
                    "workflow_duration_ms": metadata.get("total_duration_ms"),
                },
                "task": task,
                "assets": {
                    "task_overview": (
                        "task_overview.pdf" if (workflow / "task_overview.pdf").exists() else None
                    ),
                    "inputs": [
                        str(path.relative_to(workflow))
                        for path in sorted((workflow / "input_files").glob("*"))
                        if path.is_file()
                    ],
                    "deliverables": [
                        str(path.relative_to(workflow))
                        for path in sorted((workflow / "output_files").glob("*"))
                        if path.is_file()
                    ],
                },
                "observation": observation,
                "intent": annotation.get("intent"),
                "action_summary": annotation.get("action"),
                "details": annotation.get("details"),
                "actions": compacted,
                "quality": {"score": quality, "flags": reasons},
                "images": images,
            }
            row["messages"] = _chat_messages(task, annotation, compacted)
            rows.append(row)
            if max_examples is not None and len(rows) >= max_examples:
                break
        if max_examples is not None and len(rows) >= max_examples:
            break

    split_counts = Counter(row["split"] for row in rows)
    for name in ("all", "train", "validation", "test"):
        selected = rows if name == "all" else [row for row in rows if row["split"] == name]
        with (output_root / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in selected:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    report = {
        "schema_version": SCHEMA_VERSION,
        "workflow_count": len(workflow_paths),
        "example_count": len(rows),
        "split_counts": dict(split_counts),
        "raw_event_count": raw_events,
        "policy_event_count": policy_events,
        "policy_event_fraction": round(policy_events / raw_events, 6) if raw_events else 0.0,
        "raw_event_types": dict(event_counts.most_common()),
        "drag_reconstruction": dict(drag_stats),
        "rejected_reasons": dict(rejected.most_common()),
        "parameters": {
            "min_quality": min_quality,
            "max_segment_s": max_segment_s,
            "max_actions": max_actions,
            "observation_lead_s": observation_lead_s,
            "max_examples": max_examples,
            "train_ratio": train_ratio,
            "validation_ratio": validation_ratio,
            "extract_frames": extract_frames,
            "min_drag_distance_px": MIN_DRAG_DISTANCE_PX,
        },
        "source_repositories": sorted(
            {
                f"{item.get('repo')}@{item.get('revision')}"
                for item in source_manifest
                if item.get("repo") and item.get("revision")
            }
        ),
    }
    with (output_root / "report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def merge_split_typing(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Re-join typed values that an earlier prepare split across neighbouring narration spans.

    Rows must be in workflow/segment order. When row ``k`` ends with ``type_text`` and row ``k+1``
    (same workflow) starts with ``type_text`` and the absolute gap between the two keystrokes is
    <= TYPING_GAP_S, the text moves into row ``k``; a row left without actions is dropped.
    Returns the new rows and the number of merges performed.
    """
    result: list[dict[str, Any]] = []
    merges = 0
    for row in rows:
        previous = result[-1] if result else None
        if (
            previous is not None
            and previous["source"].get("workflow_id") == row["source"].get("workflow_id")
            and previous["source"].get("software") == row["source"].get("software")
            and previous["actions"]
            and row["actions"]
            and previous["actions"][-1]["type"] == "type_text"
            and row["actions"][0]["type"] == "type_text"
        ):
            previous_t = previous["observation"]["timestamp_s"] + previous["actions"][-1]["dt"]
            current_t = row["observation"]["timestamp_s"] + row["actions"][0]["dt"]
            # ``dt`` marks the first keystroke; approximate the previous value's end by its length.
            if 0 <= current_t - previous_t <= TYPING_GAP_S * max(1, len(previous["actions"][-1]["text"])):
                previous["actions"][-1]["text"] += row["actions"][0]["text"]
                if "messages" in previous:
                    previous["messages"] = _chat_messages(previous["task"], {"intent": previous.get("intent"), "action": previous.get("action_summary")}, previous["actions"])
                row = dict(row)
                row["actions"] = row["actions"][1:]
                merges += 1
                if not row["actions"]:
                    continue
                if "messages" in row:
                    row["messages"] = _chat_messages(row["task"], {"intent": row.get("intent"), "action": row.get("action_summary")}, row["actions"])
        result.append(row)
    return result, merges


def validate_records(paths: Iterable[Path]) -> dict[str, Any]:
    count = 0
    workflows_by_split: dict[str, set[str]] = {
        name: set() for name in ("train", "validation", "test")
    }
    errors: list[str] = []
    for path in paths:
        for row in iter_json_lines(path):
            count += 1
            row_id = str(row.get("id", f"{path}:{count}"))
            split = row.get("split")
            if split not in workflows_by_split:
                errors.append(f"{row_id}: invalid split {split!r}")
                continue
            source = row.get("source") or {}
            workflow_key = f"{source.get('software')}/{source.get('workflow_id')}"
            workflows_by_split[split].add(workflow_key)
            if not row.get("actions"):
                errors.append(f"{row_id}: empty action target")
            for action in row.get("actions", []):
                for coordinate in ("x", "y", "end_x", "end_y"):
                    value = action.get(coordinate)
                    if value is not None and not 0.0 <= value <= 1.0:
                        errors.append(f"{row_id}: {coordinate}={value} outside [0,1]")

    names = list(workflows_by_split)
    leakage: set[str] = set()
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            leakage.update(workflows_by_split[left] & workflows_by_split[right])
    if leakage:
        errors.append(f"workflow leakage across splits: {sorted(leakage)}")
    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors[:50]))
    return {
        "record_count": count,
        "workflow_counts": {name: len(items) for name, items in workflows_by_split.items()},
        "errors": 0,
    }
