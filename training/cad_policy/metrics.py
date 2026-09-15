"""Offline action metrics for the CAD policy (pure Python, unit-tested)."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from cad1000.policy_view import GRID_MAX, grid_to_pixels, parse_completion

DEFAULT_RADII_PX = (8, 16, 32, 64)


def _pixel_point(action: dict[str, Any], screen: dict[str, Any], prefix: str = "") -> tuple[float, float]:
    width = float(screen.get("width") or 1920)
    height = float(screen.get("height") or 1080)
    return grid_to_pixels(action[f"{prefix}x"], width), grid_to_pixels(action[f"{prefix}y"], height)


def score_prediction(
    completion: str,
    target_actions: list[dict[str, Any]],
    screen: dict[str, Any],
    *,
    radii_px: tuple[int, ...] = DEFAULT_RADII_PX,
) -> dict[str, Any]:
    """Score one model completion against the reference chunk.

    Coordinate metrics compare the first predicted action to the first target action when both
    carry a point. Sequence metrics compare the whole chunk element-wise.
    """
    result: dict[str, Any] = {
        "json_ok": False,
        "schema_ok": False,
        "parse_error": None,
        "type_ok": False,
        "sequence_types_ok": False,
        "length_ok": False,
        "exact_ok": False,
        "key_ok": None,
        "text_ok": None,
        "coord_error_norm": None,
        "coord_error_px": None,
        "within_px": {str(r): None for r in radii_px},
        "target_type": target_actions[0]["type"] if target_actions else None,
        "predicted_type": None,
    }
    predicted, error = parse_completion(completion)
    if predicted is None:
        # Distinguish a JSON failure from a schema failure for reporting.
        result["parse_error"] = error
        result["json_ok"] = not (error or "").startswith("json")
        return result
    result["json_ok"] = True
    result["schema_ok"] = True
    first_pred, first_target = predicted[0], target_actions[0]
    result["predicted_type"] = first_pred["type"]
    result["type_ok"] = first_pred["type"] == first_target["type"]
    result["length_ok"] = len(predicted) == len(target_actions)
    result["sequence_types_ok"] = [a["type"] for a in predicted] == [a["type"] for a in target_actions]
    result["exact_ok"] = predicted == target_actions
    if first_target["type"] == "key":
        result["key_ok"] = (
            first_pred["type"] == "key"
            and first_pred["key"] == first_target["key"]
            and sorted(first_pred["modifiers"]) == sorted(first_target["modifiers"])
        )
    if first_target["type"] == "type_text":
        result["text_ok"] = first_pred["type"] == "type_text" and first_pred["text"] == first_target["text"]
    if "x" in first_target and "x" in first_pred:
        tx, ty = _pixel_point(first_target, screen)
        px, py = _pixel_point(first_pred, screen)
        error_px = math.hypot(px - tx, py - ty)
        error_norm = math.hypot(
            (first_pred["x"] - first_target["x"]) / GRID_MAX,
            (first_pred["y"] - first_target["y"]) / GRID_MAX,
        )
        result["coord_error_px"] = error_px
        result["coord_error_norm"] = error_norm
        result["within_px"] = {str(r): error_px <= r for r in radii_px}
    return result


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def aggregate(scores: list[dict[str, Any]], *, radii_px: tuple[int, ...] = DEFAULT_RADII_PX) -> dict[str, Any]:
    if not scores:
        return {"count": 0}
    within = {str(r): _mean([s["within_px"][str(r)] for s in scores if s["within_px"][str(r)] is not None]) for r in radii_px}
    coord_scores = [s for s in scores if s["coord_error_px"] is not None]
    type_counts: dict[str, int] = defaultdict(int)
    for s in scores:
        if s["target_type"]:
            type_counts[s["target_type"]] += 1
    return {
        "count": len(scores),
        "json_parse_rate": _mean([float(s["json_ok"]) for s in scores]),
        "schema_valid_rate": _mean([float(s["schema_ok"]) for s in scores]),
        "action_type_accuracy": _mean([float(s["type_ok"]) for s in scores]),
        "sequence_type_accuracy": _mean([float(s["sequence_types_ok"]) for s in scores]),
        "chunk_length_accuracy": _mean([float(s["length_ok"]) for s in scores]),
        "exact_match": _mean([float(s["exact_ok"]) for s in scores]),
        "key_accuracy": _mean([float(s["key_ok"]) for s in scores if s["key_ok"] is not None]),
        "key_count": sum(1 for s in scores if s["key_ok"] is not None),
        "type_text_accuracy": _mean([float(s["text_ok"]) for s in scores if s["text_ok"] is not None]),
        "type_text_count": sum(1 for s in scores if s["text_ok"] is not None),
        "coord_count": len(coord_scores),
        "coord_error_px_mean": _mean([s["coord_error_px"] for s in coord_scores]),
        "coord_error_px_median": _median([s["coord_error_px"] for s in coord_scores]),
        "coord_error_norm_mean": _mean([s["coord_error_norm"] for s in coord_scores]),
        "within_px": within,
        "target_type_counts": dict(type_counts),
    }


def aggregate_by_group(
    scores: list[dict[str, Any]], groups: list[str], *, radii_px: tuple[int, ...] = DEFAULT_RADII_PX
) -> dict[str, Any]:
    """Per-group metrics plus an unweighted macro average across groups."""
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score, group in zip(scores, groups, strict=True):
        by_group[group].append(score)
    per_group = {name: aggregate(items, radii_px=radii_px) for name, items in sorted(by_group.items())}
    macro: dict[str, Any] = {}
    numeric_keys = [
        "json_parse_rate",
        "schema_valid_rate",
        "action_type_accuracy",
        "sequence_type_accuracy",
        "exact_match",
        "coord_error_px_mean",
        "coord_error_px_median",
    ]
    for key in numeric_keys:
        macro[key] = _mean([values.get(key) for values in per_group.values()])
    macro["within_px"] = {
        str(r): _mean([values["within_px"][str(r)] for values in per_group.values()]) for r in radii_px
    }
    return {"overall": aggregate(scores, radii_px=radii_px), "per_group": per_group, "macro": macro}
