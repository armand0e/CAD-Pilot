"""Multi-turn rendering parity with the Qwen3.5 chat template and multi-span label masks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from PIL import Image

from cad_policy.data import IGNORE_INDEX, END_TOKEN, PolicyCollator, PolicyDataset
from cad_policy.loss import sparse_lm_loss
from cad_policy.trajectory import render_conversation, supervised_spans, trajectory_messages

pytest.importorskip("transformers")


@pytest.fixture(scope="module")
def processor():
    from transformers import AutoProcessor

    try:
        return AutoProcessor.from_pretrained("Qwen/Qwen3.5-9B", local_files_only=True)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Qwen3.5-9B processor not cached: {error}")


def step(i: int, completion: str) -> dict:
    return {"id": f"s{i}", "intent": f"intent {i}", "actions": json.loads(completion)["actions"], "completion": completion, "source_actions": []}


@pytest.fixture
def trajectory_dir(tmp_path: Path) -> Path:
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        Image.new("RGB", (640, 480), (30, 30, 30)).save(tmp_path / name)
    record = {
        "schema_version": "cad-trajectory-1.0",
        "id": "solidworks--wf--w0001",
        "split": "train",
        "source": {"software": "solidworks", "workflow_id": "wf", "platform": "windows"},
        "software": "solidworks",
        "application": "SOLIDWORKS",
        "screen": {"width": 1920, "height": 1080},
        "task": {"application": "SOLIDWORKS", "task": "Bracket", "description": "Model it.", "requirements": ["R1"]},
        "first_step_number": 4,
        "total_steps": 6,
        "history_lines": ["1. open -> {\"actions\":[{\"type\":\"key\",\"key\":\"Enter\",\"modifiers\":[]}]}", "2. x -> {}"],
        "omitted_history_steps": 1,
        "images": ["a.jpg", "b.jpg", "c.jpg"],
        "steps": [
            step(4, '{"actions":[{"type":"click","x":10,"y":20,"button":"left"}]}'),
            step(5, '{"actions":[{"type":"type_text","text":"25"}]}'),
            step(6, '{"actions":[{"type":"scroll","x":1,"y":2,"delta_x":0,"delta_y":-1}]}'),
        ],
    }
    (tmp_path / "train.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return tmp_path


def test_render_matches_chat_template_for_generation_prompt(processor, trajectory_dir: Path) -> None:
    record = json.loads((trajectory_dir / "train.jsonl").read_text())
    messages = trajectory_messages(record, {"include_requirements": True, "include_description": True})
    ours = render_conversation(messages, drop_final_assistant=True)
    reference = processor.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    assert ours == reference
    full = render_conversation(messages)
    assert full.startswith(reference) and full.endswith(messages[-1]["content"] + END_TOKEN)
    assert full.count("<|image_pad|>") == 3


def test_spans_cover_every_completion(processor, trajectory_dir: Path) -> None:
    dataset = PolicyDataset(trajectory_dir / "train.jsonl", processor, max_total_tokens=8192, min_pixels=65536, max_pixels=1048576)
    assert len(dataset) == 1 and dataset.plans[0]["steps"] == 3
    encoded = dataset.encode(0)
    spans = supervised_spans(encoded["input_ids"], processor.tokenizer)
    assert len(spans) == 3
    supervised = encoded["labels"] != IGNORE_INDEX
    expected = "".join(s["completion"] + END_TOKEN for s in dataset.records[0]["steps"])
    assert processor.tokenizer.decode(encoded["input_ids"][supervised]) == expected
    image_id = processor.tokenizer.convert_tokens_to_ids("<|image_pad|>")
    assert not bool(((encoded["input_ids"] == image_id) & supervised).any())
    assert encoded["input_ids"].numel() == dataset.plans[0]["estimated_total_tokens"]
    prompt = dataset.encode(0, with_completion=False)["input_ids"]
    assert torch.equal(prompt, encoded["input_ids"][: prompt.numel()])
    assert processor.tokenizer.decode(prompt[-8:]).endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    target = dataset.target(0)
    assert target["id"] == "s6" and target["history_steps"] == 2


def test_budget_trims_trailing_steps(processor, trajectory_dir: Path) -> None:
    one_image = 20 * 15  # 640x480 -> 20x15 tokens
    dataset = PolicyDataset(trajectory_dir / "train.jsonl", processor, max_total_tokens=2 * one_image + 750, min_pixels=65536, max_pixels=1048576)
    assert dataset.plans[0]["steps"] == 2 and dataset.plans[0]["steps_dropped"] == 1
    assert dataset.encode(0)["supervised_spans"] == 2


def test_sparse_loss_matches_dense_on_tiny_model() -> None:
    torch.manual_seed(0)

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Embedding(50, 8)
            self.lm_head = torch.nn.Linear(8, 50, bias=False)
            outer = self

            class Inner(torch.nn.Module):
                def forward(self, input_ids, **_):
                    class Out:
                        last_hidden_state = outer.embed(input_ids)

                    return Out()

            self.model = Inner()

    model = Tiny()
    ids = torch.randint(0, 50, (2, 12))
    labels = ids.clone()
    labels[:, :5] = IGNORE_INDEX
    labels[1, 9:] = IGNORE_INDEX
    dense_logits = model.lm_head(model.embed(ids))[:, :-1].float()
    dense = torch.nn.functional.cross_entropy(dense_logits.reshape(-1, 50), labels[:, 1:].reshape(-1), ignore_index=IGNORE_INDEX)
    sparse = sparse_lm_loss(model, {"input_ids": ids, "labels": labels})
    assert torch.allclose(dense, sparse)
    n = int((labels[:, 1:] != IGNORE_INDEX).sum())
    assert torch.allclose(sparse_lm_loss(model, {"input_ids": ids, "labels": labels}, num_items_in_batch=n), dense)
    batch = PolicyCollator(0)([{"input_ids": ids[0], "attention_mask": torch.ones(12, dtype=torch.long), "pixel_values": torch.zeros(1, 4), "image_grid_thw": torch.tensor([[1, 1, 1]]), "labels": labels[0]}])
    assert batch["labels"].shape == (1, 12)
