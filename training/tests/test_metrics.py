from __future__ import annotations

from cad_policy.metrics import aggregate, aggregate_by_group, score_prediction

SCREEN = {"width": 1920, "height": 1080}
CLICK = [{"type": "click", "x": 500, "y": 500, "button": "left"}]


def test_exact_click_scores_perfectly() -> None:
    score = score_prediction('{"actions":[{"type":"click","x":500,"y":500,"button":"left"}]}', CLICK, SCREEN)
    assert score["json_ok"] and score["schema_ok"] and score["type_ok"] and score["exact_ok"]
    assert score["coord_error_px"] == 0 and score["within_px"] == {"8": True, "16": True, "32": True, "64": True}


def test_pixel_error_uses_screen_size() -> None:
    # 10 grid units on a 1920-wide screen ~ 19.2 px horizontally.
    score = score_prediction('{"actions":[{"type":"click","x":510,"y":500,"button":"left"}]}', CLICK, SCREEN)
    assert abs(score["coord_error_px"] - 1920 * 10 / 999) < 1e-6
    assert score["within_px"] == {"8": False, "16": False, "32": True, "64": True}
    assert not score["exact_ok"] and score["type_ok"]


def test_invalid_outputs_are_classified() -> None:
    bad_json = score_prediction("click at 500,500", CLICK, SCREEN)
    assert not bad_json["json_ok"] and not bad_json["schema_ok"]
    bad_schema = score_prediction('{"actions":[{"type":"click","x":500,"y":500}]}', CLICK, SCREEN)
    assert bad_schema["json_ok"] and not bad_schema["schema_ok"]
    prose = score_prediction('Sure! {"actions":[]}', CLICK, SCREEN)
    assert not prose["json_ok"]


def test_key_and_text_targets() -> None:
    key_target = [{"type": "key", "key": "Enter", "modifiers": []}]
    assert score_prediction('{"actions":[{"type":"key","key":"Enter","modifiers":[]}]}', key_target, SCREEN)["key_ok"]
    assert not score_prediction('{"actions":[{"type":"key","key":"Enter","modifiers":["ctrl"]}]}', key_target, SCREEN)["key_ok"]
    text_target = [{"type": "type_text", "text": "25.4"}]
    assert score_prediction('{"actions":[{"type":"type_text","text":"25.4"}]}', text_target, SCREEN)["text_ok"]
    assert score_prediction('{"actions":[{"type":"type_text","text":"25"}]}', text_target, SCREEN)["text_ok"] is False


def test_aggregation_and_macro_average() -> None:
    good = score_prediction('{"actions":[{"type":"click","x":500,"y":500,"button":"left"}]}', CLICK, SCREEN)
    bad = score_prediction("nope", CLICK, SCREEN)
    overall = aggregate([good, bad])
    assert overall["json_parse_rate"] == 0.5 and overall["coord_count"] == 1
    grouped = aggregate_by_group([good, good, good, bad], ["autocad", "autocad", "autocad", "catia"])
    assert grouped["overall"]["json_parse_rate"] == 0.75
    assert grouped["macro"]["json_parse_rate"] == 0.5  # unweighted across the two applications
    assert grouped["per_group"]["catia"]["count"] == 1
