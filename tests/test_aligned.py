import json
from collections import Counter

from cad1000.aligned import atomic_events
from cad1000.policy_view import convert_action, validate_policy_actions
from cad1000.project_splits import project_split_plan


def events(tmp_path, values):
    rows = [{"type": "screen_config", "timestamp": 0, "screens": [{"width": 100, "height": 100}]}]
    for kind, t, extra in values:
        rows.append({"type": kind, "timestamp": t * 1000, "appName": "SolidWorks", **extra})
    path = tmp_path / "events.json"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return atomic_events(path, software="solidworks", application="SolidWorks", platform="windows")


def key(text):
    return {"keyCode": ord(text.upper()), "characters": text, "modifiers": 0}


def test_typing_uses_actual_last_key_time(tmp_path):
    atoms, _ = events(tmp_path, [("key", 1, key("1")), ("key", 1.7, key("2")), ("key", 2.4, key("3")), ("key", 3.2, key("4"))])
    assert [a["text"] for a in atoms] == ["123", "4"]
    assert atoms[0]["t"] == 1 and atoms[0]["_end"] == 2.4


def test_focus_privacy_and_off_app_break_typing(tmp_path):
    atoms, _ = events(tmp_path, [("key", 1, key("1")), ("active_app", 1.1, {"name": "Browser"}),
                                ("key", 1.2, key("2")), ("active_app", 1.3, {"name": "SolidWorks"}),
                                ("key", 1.4, key("3")), ("privacy_mask_start", 1.5, {}),
                                ("key", 1.6, key("4")), ("privacy_mask_end", 1.7, {}), ("key", 1.8, key("5"))])
    assert [a["text"] for a in atoms] == ["1", "3", "5"]
    assert len({a["_epoch"] for a in atoms}) == 3
    assert atoms[-1]["_lower"] == 1.7


def test_drag_retains_held_modifiers_and_release_time(tmp_path):
    atoms, _ = events(tmp_path, [("modifier_change", .9, {"modifiers": 2}),
                               ("click", 1, {"x": 10, "y": 20, "isDown": True}),
                               ("drag", 1.1, {"x": 40, "y": 50}),
                               ("click", 1.2, {"x": 40, "y": 50, "isDown": False})])
    assert len(atoms) == 1 and atoms[0]["type"] == "drag"
    target = convert_action(atoms[0], platform="windows")
    assert target["modifiers"] == ["ctrl"] and not validate_policy_actions([target])
    assert atoms[0]["_end"] == 1.2


def test_double_click_is_one_atomic_target(tmp_path):
    atoms, _ = events(tmp_path, [("click", 1, {"x": 10, "y": 10, "isDown": True}),
                               ("click", 1.01, {"x": 10, "y": 10, "isDown": False}),
                               ("click", 1.15, {"x": 10, "y": 10, "isDown": True, "clickCount": 2}),
                               ("click", 1.16, {"x": 10, "y": 10, "isDown": False})])
    assert len(atoms) == 1 and atoms[0]["clicks"] == 2 and atoms[0]["t"] == 1


def test_fractional_scroll_direction_and_zero_rejection():
    import pytest
    for delta in [.06666, -.06666]:
        action = convert_action({"type": "scroll", "x": .5, "y": .5, "delta_x": 0, "delta_y": delta}, platform="windows")
        assert action["delta_y"] == (1 if delta > 0 else -1)
    with pytest.raises(ValueError, match="zero_scroll"):
        convert_action({"type": "scroll", "x": .5, "y": .5, "delta_x": 0, "delta_y": 0}, platform="windows")


def test_unfinished_gesture_and_bad_coordinates_rejected(tmp_path):
    atoms, stats = events(tmp_path, [("click", 1, {"x": 10, "y": 10}), ("scroll", 2, {"x": -1, "y": 20, "deltaY": 1})])
    assert not atoms and stats["invalid_pointer_coordinates"] == 1


def test_project_splits_deterministic_cover_apps_and_no_artifact_leakage():
    manifests = [{"software": app, "workflow_id": str(i), "revision": "pinned", "source_files": []} for app in ["a", "b"] for i in range(10)]
    manifests[0]["source_files"] = [{"name": "shared.prt", "oid": "same"}]
    manifests[10]["source_files"] = [{"name": "shared.prt", "oid": "same"}]
    plan = project_split_plan(manifests)
    assert project_split_plan(list(reversed(manifests))) == plan
    assert plan["assignments"]["a/0"]["split"] == plan["assignments"]["b/0"]["split"]
    assert not plan["coverage_limitations"]
    for app in ["a", "b"]:
        assert set(plan["per_app"][app]) == {"train", "validation", "test"}
