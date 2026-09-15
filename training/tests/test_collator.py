"""Label-mask and token-budget tests against the real Qwen3.5 processor (CPU, no weights)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from PIL import Image

from cad_policy.data import IGNORE_INDEX, END_TOKEN, PolicyCollator, PolicyDataset, describe_mask

pytest.importorskip("transformers")


@pytest.fixture(scope="module")
def processor():
    from transformers import AutoProcessor

    try:
        return AutoProcessor.from_pretrained("Qwen/Qwen3.5-9B", local_files_only=True)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Qwen3.5-9B processor not cached: {error}")


def policy_row(identifier: str, image: str, requirements: list[str]) -> dict:
    return {
        "schema_version": "cad-policy-1.0",
        "id": identifier,
        "split": "train",
        "source": {"software": "solidworks", "workflow_id": "wf", "platform": "windows"},
        "software": "solidworks",
        "application": "SOLIDWORKS",
        "screen": {"width": 1920, "height": 1080},
        "image": image,
        "task": {"application": "SOLIDWORKS", "task": "Bracket", "description": "Model a bracket.", "requirements": requirements},
        "intent": "Click the sketch tool.",
        "actions": [{"type": "click", "x": 28, "y": 128, "button": "left"}],
        "completion": '{"actions":[{"type":"click","x":28,"y":128,"button":"left"}]}',
        "audit": {"action_summary": "hidden"},
    }


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    Image.new("RGB", (1280, 720), (200, 200, 200)).save(tmp_path / "wide.jpg")
    Image.new("RGB", (640, 480), (10, 10, 10)).save(tmp_path / "small.jpg")
    rows = [
        policy_row("a", "wide.jpg", ["R1", "R2"]),
        policy_row("b", "small.jpg", []),
        policy_row("c", "wide.jpg", ["very long requirement " * 400]),
    ]
    (tmp_path / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def test_mask_covers_only_completion_and_image_tokens_match(processor, policy_dir: Path) -> None:
    dataset = PolicyDataset(policy_dir / "train.jsonl", processor, max_total_tokens=8192, min_pixels=65536, max_pixels=1048576)
    assert len(dataset) == 3 and not dataset.excluded
    tokenizer = processor.tokenizer
    image_token_id = tokenizer.convert_tokens_to_ids("<|image_pad|>")
    for index in range(len(dataset)):
        encoded = dataset.encode(index)
        plan = dataset.plans[index]
        assert int((encoded["input_ids"] == image_token_id).sum()) == plan["image_tokens"]
        assert encoded["input_ids"].numel() == plan["estimated_total_tokens"]
        supervised = encoded["labels"] != IGNORE_INDEX
        assert tokenizer.decode(encoded["input_ids"][supervised]) == dataset.records[index]["completion"] + END_TOKEN
        assert not bool(((encoded["input_ids"] == image_token_id) & supervised).any())
        prompt_text = tokenizer.decode(encoded["input_ids"][: encoded["prompt_length"]])
        assert prompt_text.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
        assert "hidden" not in prompt_text
        prompt_only = dataset.encode(index, with_completion=False)["input_ids"]
        assert torch.equal(prompt_only, encoded["input_ids"][: prompt_only.numel()])
    # 1280x720 -> 1280x704 (720/32 = 22.5 rounds half-even to 22) -> 40 x 22 = 880 image tokens
    assert dataset.plans[0]["image_tokens"] == 880


def test_budget_drops_requirements_then_rejects(processor, policy_dir: Path) -> None:
    # Includes the explicit action-schema examples in the shared system prompt.
    dataset = PolicyDataset(policy_dir / "train.jsonl", processor, max_total_tokens=1600, min_pixels=65536, max_pixels=1048576)
    variants = {record["id"]: plan["variant_index"] for record, plan in zip(dataset.records, dataset.plans, strict=True)}
    assert variants["b"] == 0 and variants["c"] > 0
    tiny = PolicyDataset(policy_dir / "train.jsonl", processor, max_total_tokens=300, min_pixels=65536, max_pixels=1048576)
    assert len(tiny) == 0 and {item["reason"] for item in tiny.excluded} == {"over_budget"}
    report = dataset.length_report()
    assert report["total_tokens"]["max"] <= 1600


def test_collator_pads_right_and_concatenates_pixels(processor, policy_dir: Path) -> None:
    dataset = PolicyDataset(policy_dir / "train.jsonl", processor, max_total_tokens=8192, min_pixels=65536, max_pixels=1048576)
    batch = PolicyCollator(processor.tokenizer.pad_token_id)([dataset[0], dataset[1]])
    assert batch["input_ids"].shape[0] == 2 and batch["labels"].shape == batch["input_ids"].shape
    assert batch["image_grid_thw"].shape == (2, 3)
    assert batch["mm_token_type_ids"].shape == batch["input_ids"].shape
    assert int(batch["mm_token_type_ids"].sum()) == int((batch["input_ids"] == processor.tokenizer.convert_tokens_to_ids("<|image_pad|>")).sum())
    assert batch["pixel_values"].shape[0] == int(batch["image_grid_thw"].prod(dim=1).sum())
    padded = batch["attention_mask"][1] == 0
    assert bool((batch["labels"][1][padded] == IGNORE_INDEX).all())
    audit = describe_mask(batch, processor.tokenizer, row=0)
    assert audit["image_tokens_supervised"] == 0 and audit["supervised_text"].startswith('{"actions":')
    assert dataset.sampling_weights(0.5) == pytest.approx([1 / 3] * 3)
