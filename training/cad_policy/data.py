"""Policy dataset, token-budget enforcement and the loss-masked multimodal collator.

Why not TRL's ``DataCollatorForVisionLanguageModeling``: for ``messages`` data it sets
``labels = input_ids`` (loss on system, user and image tokens). The handoff requires loss only on
the assistant completion, so this module renders the prompt with the real Qwen3.5 chat template
(``enable_thinking=False``), appends the strict-JSON completion plus ``<|im_end|>``, and masks
everything before the completion with ``-100``. The mask is verified by a unit test and again by
``preflight.py`` on real batches.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from cad1000.imagetokens import smart_resize as _smart_resize
from cad1000.policy_view import app_balanced_weights, build_prompt_messages, iter_policy_rows

from .trajectory import labels_from_spans, render_conversation, supervised_spans, trajectory_messages

IGNORE_INDEX = -100
END_TOKEN = "<|im_end|>"
PROMPT_VARIANTS: tuple[dict[str, bool], ...] = (
    {"include_requirements": True, "include_description": True},
    {"include_requirements": False, "include_description": True},
    {"include_requirements": False, "include_description": False},
)


smart_resize = _smart_resize


class ImageTokenizer:
    """Predicts the number of image tokens the processor will emit for a given image size."""

    def __init__(self, processor: Any, *, min_pixels: int, max_pixels: int) -> None:
        image_processor = processor.image_processor
        self.patch_size = int(getattr(image_processor, "patch_size", 16))
        self.merge_size = int(getattr(image_processor, "merge_size", 2))
        self.min_pixels = int(min_pixels)
        self.max_pixels = int(max_pixels)
        # Apply the budget to the real processor so training/eval use the same resize rule.
        image_processor.size = {"shortest_edge": self.min_pixels, "longest_edge": self.max_pixels}
        for attribute, value in (("min_pixels", self.min_pixels), ("max_pixels", self.max_pixels)):
            if hasattr(image_processor, attribute):
                setattr(image_processor, attribute, value)

    def resized(self, width: int, height: int) -> tuple[int, int]:
        factor = self.patch_size * self.merge_size
        h_bar, w_bar = smart_resize(height, width, factor=factor, min_pixels=self.min_pixels, max_pixels=self.max_pixels)
        return w_bar, h_bar

    def token_count(self, width: int, height: int) -> int:
        w_bar, h_bar = self.resized(width, height)
        factor = self.patch_size * self.merge_size
        return (h_bar // factor) * (w_bar // factor)


def render_prompt(processor: Any, record: dict[str, Any], variant: dict[str, bool]) -> str:
    messages = build_prompt_messages(record, **variant)
    return processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def is_trajectory(record: dict[str, Any]) -> bool:
    return str(record.get("schema_version", "")).startswith("cad-trajectory")


class PolicyDataset(torch.utils.data.Dataset):
    """Loads ``cad-policy-1.0`` rows and encodes them lazily with the Qwen processor."""

    def __init__(
        self,
        jsonl_path: Path,
        processor: Any,
        *,
        max_total_tokens: int,
        min_pixels: int,
        max_pixels: int,
        max_examples: int | None = None,
        seed: int = 42,
        selection: str = "random",
        require_full_coverage: bool = False,
    ) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.base_dir = self.jsonl_path.parent
        self.processor = processor
        self.tokenizer = processor.tokenizer
        self.max_total_tokens = int(max_total_tokens)
        self.require_full_coverage = require_full_coverage
        self.image_tokens = ImageTokenizer(processor, min_pixels=min_pixels, max_pixels=max_pixels)
        rows = list(iter_policy_rows(self.jsonl_path))
        if max_examples is not None and len(rows) > max_examples:
            if selection == "app_stratified":
                from .sampling import stratified_indices
                keep = stratified_indices(rows, max_examples, seed)
            else:
                generator = torch.Generator().manual_seed(seed)
                keep = torch.randperm(len(rows), generator=generator)[:max_examples].tolist()
            rows = [rows[index] for index in sorted(keep)]
        self.records: list[dict[str, Any]] = []
        self.plans: list[dict[str, Any]] = []
        self.excluded: list[dict[str, Any]] = []
        self.image_token_counts: list[int] = []
        for row in rows:
            plan = self.plan(row)
            if plan is None:
                continue
            self.records.append(row)
            self.plans.append(plan)
        self.variant_counts = Counter(plan["variant_index"] for plan in self.plans)
        if require_full_coverage and (self.excluded or any(p.get("steps_dropped", 0) for p in self.plans)):
            raise ValueError("Coverage gate failed: excluded rows or budget-trimmed steps; rebuild smaller windows")

    # ---- planning (cheap, no pixel processing) -------------------------------------------
    def completion_text(self, record: dict[str, Any]) -> str:
        return record["completion"] + END_TOKEN

    def plan(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the longest prompt variant that fits the budget, or ``None`` if none fits."""
        if is_trajectory(record):
            return self.plan_trajectory(record)
        image_path = self.base_dir / record["image"]
        if not image_path.exists():
            self.excluded.append({"id": record["id"], "reason": "missing_image"})
            return None
        width, height = image_size(image_path)
        n_image = self.image_tokens.token_count(width, height)
        completion_ids = self.tokenizer(self.completion_text(record), add_special_tokens=False)["input_ids"]
        for index, variant in enumerate(PROMPT_VARIANTS):
            prompt = render_prompt(self.processor, record, variant)
            prompt_ids = self.tokenizer(prompt, add_special_tokens=False)["input_ids"]
            # The single <|image_pad|> placeholder expands to n_image tokens.
            total = len(prompt_ids) - 1 + n_image + len(completion_ids)
            if total <= self.max_total_tokens:
                return {
                    "variant_index": index,
                    "variant": variant,
                    "estimated_total_tokens": total,
                    "image_tokens": n_image,
                    "completion_tokens": len(completion_ids),
                    "image_size": [width, height],
                }
        self.excluded.append({"id": record["id"], "reason": "over_budget"})
        return None

    def plan_trajectory(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Fit a window: longest prompt variant first, then drop trailing steps until it fits."""
        image_tokens = []
        for image in record["images"]:
            path = self.base_dir / image
            if not path.exists():
                self.excluded.append({"id": record["id"], "reason": "missing_image"})
                return None
            width, height = image_size(path)
            image_tokens.append(self.image_tokens.token_count(width, height))
        n_steps = len(record["steps"])
        while n_steps >= 1:
            trimmed = self._trimmed(record, n_steps)
            for index, variant in enumerate(PROMPT_VARIANTS):
                text = render_conversation(trajectory_messages(trimmed, variant))
                text_tokens = len(self.tokenizer(text, add_special_tokens=False)["input_ids"])
                total = text_tokens - n_steps + sum(image_tokens[:n_steps])
                if total <= self.max_total_tokens:
                    return {
                        "variant_index": index,
                        "variant": variant,
                        "estimated_total_tokens": total,
                        "image_tokens": sum(image_tokens[:n_steps]),
                        "completion_tokens": sum(len(self.tokenizer(step["completion"] + END_TOKEN, add_special_tokens=False)["input_ids"]) for step in trimmed["steps"]),
                        "image_size": list(image_size(self.base_dir / record["images"][0])),
                        "steps": n_steps,
                        "steps_dropped": len(record["steps"]) - n_steps,
                    }
            n_steps -= 1
        self.excluded.append({"id": record["id"], "reason": "over_budget"})
        return None

    @staticmethod
    def _trimmed(record: dict[str, Any], n_steps: int) -> dict[str, Any]:
        if n_steps == len(record["steps"]):
            return record
        return record | {"steps": record["steps"][:n_steps], "images": record["images"][:n_steps]}

    def target(self, index: int) -> dict[str, Any]:
        """The action the evaluator scores: the (last) step of the record."""
        record, plan = self.records[index], self.plans[index]
        if is_trajectory(record):
            step = record["steps"][plan["steps"] - 1]
            return {"id": step["id"], "actions": step["actions"], "completion": step["completion"], "intent": step["intent"], "screen": record["screen"], "software": record["software"], "history_steps": plan["steps"] - 1}
        return {"id": record["id"], "actions": record["actions"], "completion": record["completion"], "intent": record["intent"], "screen": record["screen"], "software": record["software"], "history_steps": 0}

    # ---- encoding ---------------------------------------------------------------------
    def encode_trajectory(self, index: int, *, with_completion: bool) -> dict[str, Any]:
        record, plan = self.records[index], self.plans[index]
        trimmed = self._trimmed(record, plan["steps"])
        messages = trajectory_messages(trimmed, plan["variant"])
        text = render_conversation(messages, drop_final_assistant=not with_completion)
        images = [Image.open(self.base_dir / image).convert("RGB") for image in trimmed["images"]]
        encoded = self.processor(text=[text], images=images, return_tensors="pt", padding=False)
        input_ids = encoded["input_ids"][0]
        result = {
            "input_ids": input_ids,
            "attention_mask": encoded["attention_mask"][0],
            "pixel_values": encoded["pixel_values"].to(torch.bfloat16),
            "image_grid_thw": encoded["image_grid_thw"],
        }
        if "mm_token_type_ids" in encoded:
            result["mm_token_type_ids"] = encoded["mm_token_type_ids"][0]
        if with_completion:
            spans = supervised_spans(input_ids, self.tokenizer)
            if len(spans) != len(trimmed["steps"]):
                raise RuntimeError(f"{record['id']}: found {len(spans)} supervised spans for {len(trimmed['steps'])} steps")
            result["labels"] = labels_from_spans(input_ids, spans)
            result["prompt_length"] = spans[0][0]
            result["supervised_spans"] = len(spans)
            if input_ids.numel() != plan["estimated_total_tokens"]:
                raise RuntimeError(f"{record['id']}: {input_ids.numel()} tokens != planned {plan['estimated_total_tokens']}")
        if input_ids.numel() > self.max_total_tokens:
            raise RuntimeError(f"{record['id']}: {input_ids.numel()} tokens exceed budget {self.max_total_tokens}")
        return result

    def encode(self, index: int, *, with_completion: bool = True) -> dict[str, Any]:
        record, plan = self.records[index], self.plans[index]
        if is_trajectory(record):
            return self.encode_trajectory(index, with_completion=with_completion)
        image = Image.open(self.base_dir / record["image"]).convert("RGB")
        prompt = render_prompt(self.processor, record, plan["variant"])
        completion = self.completion_text(record) if with_completion else ""
        encoded = self.processor(text=[prompt + completion], images=[image], return_tensors="pt", padding=False)
        input_ids = encoded["input_ids"][0]
        result = {
            "input_ids": input_ids,
            "attention_mask": encoded["attention_mask"][0],
            "pixel_values": encoded["pixel_values"].to(torch.bfloat16),
            "image_grid_thw": encoded["image_grid_thw"],
        }
        if "mm_token_type_ids" in encoded:  # required by Qwen3.5 M-RoPE in transformers >= 5
            result["mm_token_type_ids"] = encoded["mm_token_type_ids"][0]
        if with_completion:
            completion_ids = self.tokenizer(completion, add_special_tokens=False, return_tensors="pt")["input_ids"][0]
            n_completion = completion_ids.numel()
            if not torch.equal(input_ids[-n_completion:], completion_ids):
                raise RuntimeError(f"{record['id']}: completion tokens are not a clean suffix; cannot mask prompt")
            labels = torch.full_like(input_ids, IGNORE_INDEX)
            labels[-n_completion:] = input_ids[-n_completion:]
            result["labels"] = labels
            result["prompt_length"] = input_ids.numel() - n_completion
        if input_ids.numel() > self.max_total_tokens:
            raise RuntimeError(
                f"{record['id']}: {input_ids.numel()} tokens exceed budget {self.max_total_tokens} "
                f"(planned {plan['estimated_total_tokens']}); image-token estimate is wrong"
            )
        return result

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.encode(index)

    # ---- reporting ------------------------------------------------------------------------
    def sampling_weights(self, temperature: float) -> list[float]:
        return app_balanced_weights([str(r["software"]) for r in self.records], temperature=temperature)

    def length_report(self) -> dict[str, Any]:
        totals = sorted(plan["estimated_total_tokens"] for plan in self.plans)
        images = sorted(plan["image_tokens"] for plan in self.plans)
        completions = sorted(plan["completion_tokens"] for plan in self.plans)

        def percentiles(values: list[int]) -> dict[str, int]:
            if not values:
                return {}
            pick = lambda f: values[min(len(values) - 1, round(f * (len(values) - 1)))]  # noqa: E731
            return {"min": values[0], "p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99), "max": values[-1]}

        supervised_steps = sum(plan.get("steps", 1) for plan in self.plans)
        return {
            "examples": len(self.records),
            "supervised_steps": supervised_steps,
            "steps_dropped_for_budget": sum(plan.get("steps_dropped", 0) for plan in self.plans),
            "excluded": Counter(item["reason"] for item in self.excluded),
            "max_total_tokens": self.max_total_tokens,
            "prompt_variant_counts": {str(k): v for k, v in sorted(self.variant_counts.items())},
            "total_tokens": percentiles(totals),
            "image_tokens": percentiles(images),
            "completion_tokens": percentiles(completions),
            "image_sizes": dict(Counter("x".join(map(str, plan["image_size"])) for plan in self.plans).most_common(5)),
            "software_counts": dict(Counter(str(r["software"]) for r in self.records).most_common()),
        }


class PolicyCollator:
    """Right-pads encoded examples; pixel tensors are concatenated along the patch axis."""

    def __init__(self, pad_token_id: int) -> None:
        self.pad_token_id = int(pad_token_id)

    def __call__(self, examples: Sequence[dict[str, Any]]) -> dict[str, torch.Tensor]:
        length = max(example["input_ids"].numel() for example in examples)
        batch_size = len(examples)
        input_ids = torch.full((batch_size, length), self.pad_token_id, dtype=torch.long)
        attention_mask = torch.zeros((batch_size, length), dtype=torch.long)
        labels = torch.full((batch_size, length), IGNORE_INDEX, dtype=torch.long)
        has_mm_types = all("mm_token_type_ids" in example for example in examples)
        mm_token_type_ids = torch.zeros((batch_size, length), dtype=torch.long)
        for row, example in enumerate(examples):
            n = example["input_ids"].numel()
            input_ids[row, :n] = example["input_ids"]
            attention_mask[row, :n] = example["attention_mask"]
            if "labels" in example:
                labels[row, :n] = example["labels"]
            if has_mm_types:
                mm_token_type_ids[row, :n] = example["mm_token_type_ids"]
        batch = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "pixel_values": torch.cat([example["pixel_values"] for example in examples], dim=0),
            "image_grid_thw": torch.cat([example["image_grid_thw"] for example in examples], dim=0),
        }
        if has_mm_types:
            batch["mm_token_type_ids"] = mm_token_type_ids
        if any("labels" in example for example in examples):
            batch["labels"] = labels
        return batch


def describe_mask(batch: dict[str, torch.Tensor], tokenizer: Any, row: int = 0) -> dict[str, Any]:
    """Human-readable audit of which tokens carry loss in one collated row."""
    ids = batch["input_ids"][row]
    labels = batch["labels"][row]
    supervised = labels != IGNORE_INDEX
    image_token_id = tokenizer.convert_tokens_to_ids("<|image_pad|>")
    return {
        "sequence_length": int(batch["attention_mask"][row].sum()),
        "supervised_tokens": int(supervised.sum()),
        "image_tokens": int((ids == image_token_id).sum()),
        "image_tokens_supervised": int(((ids == image_token_id) & supervised).sum()),
        "supervised_text": tokenizer.decode(ids[supervised]),
        "last_prompt_tokens": tokenizer.decode(ids[: int((~supervised).sum())][-12:]),
    }


def load_policy_dataset(config: dict[str, Any], processor: Any, split_file: str, *, max_examples: int | None) -> PolicyDataset:
    from .config import resolve_path

    policy_dir = resolve_path(config["data"]["policy_dir"])
    return PolicyDataset(
        policy_dir / split_file,
        processor,
        max_total_tokens=config["data"]["max_total_tokens"],
        min_pixels=config["data"]["image"]["min_pixels"],
        max_pixels=config["data"]["image"]["max_pixels"],
        max_examples=max_examples,
        seed=config["train"]["seed"],
        selection=config["data"].get("selection", "random"),
        require_full_coverage=config["data"].get("require_full_coverage", False),
    )


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
