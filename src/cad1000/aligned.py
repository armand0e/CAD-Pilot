"""Versioned action-aligned preparation. Never repairs labels after frame capture.

Video observations use actual presentation timestamps, not an assumed constant frame rate.
The legacy prepare/merge path remains available for reproducing baseline data.
"""
from __future__ import annotations

import bisect
import json
import hashlib
import math
from collections import Counter
from pathlib import Path

from .keys import MODIFIER_BITS, MODIFIER_KEY_NAMES, key_name
from .policy_view import convert_action, validate_policy_actions
from .prepare import app_matches, iter_json_lines, MIN_DRAG_DISTANCE_PX

RECIPE = "action-aligned-v3.2"
SCHEMA = "cad-vla-2.0"
# Recording/event clocks are only precise to milliseconds. A 10 ms safety margin also rejects
# actions with no unambiguous pre-action video frame. Do not infer missing observations.
FRAME_MARGIN_S = .010
MAX_FRAME_AGE_S = .250
OFF_TASK = ("system settings", "power status", "screen recording", "recording software",
            "wi-fi", "wifi settings", "audio volume", "taskbar", "notification center")
CONTROL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|image_pad|>")


def atomic_events(path: Path, *, software: str, application: str, platform: str):
    """Return atomic neutral actions, plus reason counts. Keep input boundaries even on rejects."""
    out, stats = [], Counter()
    first_ms = None
    width, height, screens = 1920, 1080, 1
    active, modifiers, privacy, epoch = None, 0, False, 0
    pending = None
    barrier_time = 0.0
    previous_end = 0.0

    def boundary(t):
        nonlocal epoch, barrier_time, pending
        epoch += 1
        barrier_time = max(barrier_time, t)
        if pending is not None:
            stats["incomplete_pointer_gesture"] += 1
        pending = None

    def append(action):
        nonlocal previous_end
        action["_lower"] = max(previous_end, barrier_time)
        previous_end = max(previous_end, action["_end"])
        prev = out[-1] if out else None
        same = prev and all(prev[k] == action[k] for k in ("_epoch", "_screen", "modifiers"))
        gap = action["t"] - prev["_end"] if prev else math.inf
        # Group only contiguous inputs; off-app/focus/privacy events increment the epoch.
        if same and prev["type"] == action["type"] == "type_text" and 0 <= gap <= .75:
            prev["text"] += action["text"]
            prev["_end"] = action["_end"]
            stats["typing_keys_grouped"] += 1
            return
        if same and prev["type"] == action["type"] == "scroll" and 0 <= gap <= .15:
            same_place = max(abs(prev[k] - action[k]) for k in ("x", "y")) < .002
            same_direction = all(prev[k] * action[k] >= 0 for k in ("delta_x", "delta_y"))
            if same_place and same_direction and all(abs(prev[k] + action[k]) <= 10 for k in ("delta_x", "delta_y")):
                for k in ("delta_x", "delta_y"):
                    prev[k] += action[k]
                prev["_end"] = action["_end"]
                stats["scroll_events_grouped"] += 1
                return
        click_count = action.pop("_click_count", 1)
        if same and prev["type"] == action["type"] == "click" and click_count == prev.get("clicks", 1) + 1 and click_count <= 3 and 0 <= gap <= .5:
            if prev["button"] == action["button"] and max(abs(prev[k] - action[k]) for k in ("x", "y")) < .002:
                prev["clicks"] = click_count
                prev["_end"] = action["_end"]
                stats["multi_clicks_grouped"] += 1
                return
        out.append(action)

    for event in iter_json_lines(path):
        stats["raw_events"] += 1
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp):
            stats["invalid_timestamp"] += 1
            continue
        if first_ms is None:
            first_ms = timestamp
        t = float(event.get("video_t_ms", timestamp - first_ms)) / 1000
        if not math.isfinite(t) or t < 0:
            stats["invalid_timestamp"] += 1
            continue
        kind = event.get("type")
        if kind == "screen_config":
            config = event.get("screens") or []
            if config:
                size = (int(config[0].get("width", width)), int(config[0].get("height", height)), len(config))
                if size != (width, height, screens):
                    boundary(t)
                width, height, screens = size
            continue
        if kind == "active_app":
            name = event.get("name") or event.get("appName")
            if name != active:
                boundary(t)
                active = name
            continue
        if kind in {"window_title_changed", "window_moved", "window_resized", "input_source_change"}:
            boundary(t)
            continue
        if kind in {"privacy_mask_start", "privacy_mask_end"}:
            privacy = kind == "privacy_mask_start"
            boundary(t)
            continue
        if kind == "modifier_change":
            modifiers = int(event.get("modifiers") or 0)
            continue
        if kind not in {"click", "drag", "scroll", "key"}:
            continue
        stats["input_events"] += 1
        if privacy or screens != 1 or event.get("screen", 0) != 0:
            stats["privacy_or_multiscreen"] += 1
            boundary(t)
            continue
        event_app = event.get("appName") or active
        if not event_app or not app_matches(event_app, software, application) or (active and not app_matches(active, software, application)):
            stats["off_target_or_unknown_app"] += 1
            boundary(t)
            continue
        held = int(event.get("modifiers", modifiers) or 0)
        if held & ~15:
            stats["unknown_modifiers"] += 1
            boundary(t)
            continue
        if kind in {"click", "drag", "scroll"}:
            coords = [event.get("x"), event.get("y")]
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in coords) or not (0 <= coords[0] < width and 0 <= coords[1] < height):
                stats["invalid_pointer_coordinates"] += 1
                boundary(t)
                continue
            x, y = coords[0] / width, coords[1] / height
        if kind == "drag":
            if pending is not None:
                pending["end_x"], pending["end_y"] = x, y
            continue
        if kind == "click":
            if event.get("isDown", True):
                if pending is not None:
                    boundary(t)
                pending = {"type": "click", "x": x, "y": y, "button": event.get("button", "left"),
                           "modifiers": held, "clicks": 1, "_click_count": int(event.get("clickCount") or 1),
                           "t": t, "_epoch": epoch, "_screen": [width, height]}
            elif pending is not None:
                if pending["button"] != event.get("button", "left") or held != pending["modifiers"]:
                    stats["changing_pointer_button_or_modifiers"] += 1
                    boundary(t)
                    continue
                distance = math.hypot((x - pending["x"]) * width, (y - pending["y"]) * height)
                if distance >= MIN_DRAG_DISTANCE_PX:
                    pending.update(type="drag", end_x=x, end_y=y)
                    pending.pop("clicks", None)
                else:
                    pending.pop("end_x", None)
                    pending.pop("end_y", None)
                pending["_end"] = t
                action, pending = pending, None
                append(action)
            continue
        if kind == "key" and not event.get("isDown", True):
            continue
        if pending is not None:
            stats["overlapping_pointer_and_input"] += 1
            boundary(t)
        action = {"type": kind, "t": t, "_end": t, "_epoch": epoch,
                  "_screen": [width, height], "modifiers": held}
        if kind == "key":
            name = key_name(event.get("keyCode"), event.get("characters"), platform=platform)
            if name in MODIFIER_KEY_NAMES:
                continue  # pointer snapshots carry held modifiers; no standalone modifier target
            text = str(event.get("characters") or "")
            if text and text.isprintable() and not held & ~1:
                action.update(type="type_text", text=text)
            else:
                action.update(key_code=event.get("keyCode"), text=text)
        elif kind == "scroll":
            action.update(x=x, y=y, delta_x=event.get("deltaX", 0), delta_y=event.get("deltaY", 0))
            if any(type(action[k]) not in (int, float) or not math.isfinite(action[k]) or abs(action[k]) > 10 for k in ("delta_x", "delta_y")):
                stats["unsupported_scroll_magnitude"] += 1
                boundary(t)
                continue
        append(action)
    if pending is not None:
        stats["incomplete_pointer_gesture"] += 1
    stats["atomic_actions"] = len(out)
    return out, stats


def prepare_aligned(raw: Path, output: Path, *, split: str, provenance: dict, max_actions: int | None = None):
    """Decode each video once, selecting the latest eligible actual PTS before each target."""
    import av
    from PIL import ImageStat

    output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((raw / "metadata.json").read_text())
    task = json.loads((raw / "task_desc.json").read_text())
    rubrics = json.loads((raw / "rubrics.json").read_text())
    annotations = json.loads((raw / "narration.json").read_text())["annotations"]
    software, workflow = provenance["software"], provenance["workflow_id"]
    task["requirements"] = [r["requirement"] for r in rubrics.get("rubrics", []) if r.get("requirement")]
    application = task.get("application") or software
    atoms, stats = atomic_events(raw / "events.json", software=software, application=application, platform=metadata.get("platform", "windows"))
    # Original order is meaningful. Overlapping/out-of-order annotations are rejected, not guessed.
    valid_annotations = sorted([(a["start_s"], a["end_s"], i, a) for i, a in enumerate(annotations)
                                if isinstance(a.get("start_s"), (int, float)) and isinstance(a.get("end_s"), (int, float)) and a["end_s"] > a["start_s"]])
    starts = [a[0] for a in valid_annotations]
    candidates = []
    quarantined = (output / "quarantine.jsonl").open("w")

    def reject(index, reason):
        stats[reason] += 1
        quarantined.write(json.dumps({"atomic_index": index, "reason": reason}) + "\n")

    for index, atom in enumerate(atoms):
        ai = bisect.bisect_right(starts, atom["t"]) - 1
        if ai < 0 or atom["t"] >= valid_annotations[ai][1]:
            reject(index, "outside_annotation")
            continue
        start, end, segment, annotation = valid_annotations[ai]
        if ai and valid_annotations[ai - 1][1] > start:
            reject(index, "overlapping_annotations")
            continue
        narrative = " ".join(str(annotation.get(k) or "") for k in ("intent", "action", "details")).lower()
        # The owner is the annotation where the atomic action began. A typed value may cross
        # its end, but cannot cross focus/privacy boundaries because grouping already enforces them.
        if not annotation.get("intent") or not app_matches(annotation.get("app"), software, application):
            reject(index, "off_target_annotation")
            continue
        if any(term in narrative for term in OFF_TASK) and "coordinate system settings" not in narrative:
            reject(index, "off_task_annotation")
            continue
        if any(token in json.dumps(task) + narrative + str(atom.get("text", "")) for token in CONTROL_TOKENS):
            reject(index, "chat_control_token")
            continue
        if len(str(atom.get("text", ""))) > 256:
            reject(index, "long_typed_text_review")
            continue
        try:
            converted = convert_action(atom, platform=metadata.get("platform"))
            if converted is None or validate_policy_actions([converted]):
                raise ValueError("unexecutable")
        except (ValueError, TypeError, KeyError):
            reject(index, "unexecutable_action")
            continue
        candidates.append((index, atom, segment, annotation))
    if max_actions is not None:
        # Stratify across action types and the entire workflow, not just startup screens.
        buckets = {}
        duration = max((a["t"] for _, a, _, _ in candidates), default=1)
        for candidate in candidates:
            index, action, _, _ = candidate
            bucket = (action["type"], min(4, int(5 * action["t"] / max(1, duration))))
            buckets.setdefault(bucket, []).append(candidate)
        for bucket in buckets:
            buckets[bucket].sort(key=lambda c: hashlib.sha256(f"{workflow}/{c[0]}".encode()).hexdigest())
        selected = []
        while len(selected) < max_actions and any(buckets.values()):
            for bucket in sorted(buckets):
                if buckets[bucket] and len(selected) < max_actions:
                    selected.append(buckets[bucket].pop())
        candidates = sorted(selected, key=lambda c: c[0])
    handles = {name: (output / f"{name}.jsonl").open("w") for name in ("all", "train", "validation", "test")}
    episode, previous_index, previous_epoch = -1, -2, None
    written = 0
    with av.open(str(raw / "clip.mp4")) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        stream.codec_context.thread_count = 2
        frames = iter(container.decode(video=0))
        previous_frame = next(frames, None)
        next_frame = next(frames, None)
        for index, atom, segment, annotation in candidates:
            cutoff = atom["t"] - FRAME_MARGIN_S
            while next_frame is not None and next_frame.time is not None and next_frame.time <= cutoff:
                previous_frame, next_frame = next_frame, next(frames, None)
            frame = previous_frame
            if frame is None or frame.time is None or frame.is_corrupt or not atom["_lower"] + FRAME_MARGIN_S <= frame.time <= cutoff or atom["t"] - frame.time > MAX_FRAME_AGE_S:
                reject(index, "ambiguous_or_stale_pre_action_frame")
                continue
            width, height = atom["_screen"]
            if abs((frame.width / frame.height) / (width / height) - 1) > .02:
                reject(index, "screen_frame_aspect_mismatch")
                continue
            image = frame.to_image()
            image.thumbnail((1280, 1280))
            if max(ImageStat.Stat(image.resize((64, 36))).stddev) < 2:
                reject(index, "blank_frame")
                continue
            if index != previous_index + 1 or atom["_epoch"] != previous_epoch:
                episode += 1
            previous_index, previous_epoch = index, atom["_epoch"]
            rid = f"{software}--{workflow}--a{index:07d}"
            relative = f"frames/{split}/{rid}.jpg"
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, quality=90)
            action = {k: v for k, v in atom.items() if not k.startswith("_") and k != "t"}
            action["dt"] = round(atom["t"] - frame.time, 6)
            row = {"schema_version": SCHEMA, "recipe": RECIPE, "id": rid, "split": split,
                   "source": {**provenance, "platform": metadata.get("platform"), "fps": metadata.get("fps"),
                              "segment_index": index, "annotation_index": segment, "episode_id": episode,
                              "action_start_s": atom["t"], "action_end_s": atom["_end"], "previous_input_end_s": atom["_lower"]},
                   "observation": {"timestamp_s": frame.time, "pts": frame.pts, "time_base": str(frame.time_base),
                                   "screen": {"width": width, "height": height}, "image": relative},
                   # Narration is a coarse, post-hoc span summary; it can describe trimming
                   # while a later atomic action starts a line. Never turn that into a false
                   # per-action instruction or synthesize an intent from the target label.
                   "images": [relative], "task": task,
                   "intent": "Continue the CAD task from the current screen. Recorded subtask context (may span multiple actions): " + annotation["intent"],
                   "annotation_audit": {"intent": annotation["intent"], "index": segment}, "actions": [action],
                   "quality": {"checks": ["pre_action_pts", "target_app", "no_privacy_overlap", "executable"], "semantic_review": "pending"}}
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            handles["all"].write(encoded)
            handles[split].write(encoded)
            stats[f"accepted:{action['type']}"] += 1
            written += 1
    for handle in handles.values():
        handle.close()
    quarantined.close()
    return {"recipe": RECIPE, "example_count": written, "candidate_count": len(candidates), "split_counts": {split: written},
            "counts": dict(stats), "episodes": episode + 1, "parameters": {"frame_margin_s": FRAME_MARGIN_S, "max_frame_age_s": MAX_FRAME_AGE_S,
            "max_actions": max_actions}, "decoder_version": av.__version__}
