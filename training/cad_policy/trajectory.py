"""Rendering and label masking for multi-turn ``cad-trajectory-1.0`` records.

The conversation is rendered exactly as Qwen3.5's chat template renders it at inference time:
earlier assistant turns carry no ``<think>`` block, the final assistant turn (the one a runtime
would be generating) starts with the empty ``<think>\\n\\n</think>\\n\\n`` block that
``enable_thinking=False`` produces. ``supervised_spans`` finds every assistant JSON completion
(plus its ``<|im_end|>``) in the token stream so all actions in the window receive loss.
"""

from __future__ import annotations

from typing import Any

import torch

from cad1000.trajectory_view import build_trajectory_messages

IGNORE_INDEX = -100
IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
EMPTY_THINK = "<think>\n\n</think>\n\n"
IMAGE_MARKUP = "<|vision_start|><|image_pad|><|vision_end|>"


def render_text(item: dict[str, Any]) -> str:
    content = item["content"]
    if isinstance(content, str):
        return content
    parts = []
    for piece in content:
        if piece.get("type") == "image":
            parts.append(IMAGE_MARKUP)
        else:
            parts.append(piece.get("text", ""))
    return "".join(parts)


def render_conversation(messages: list[dict[str, Any]], *, final_assistant_with_think: bool = True, drop_final_assistant: bool = False) -> str:
    """Render a system/user/assistant conversation in Qwen3.5 chat format.

    ``drop_final_assistant`` renders the prompt for generating the last assistant turn instead
    (ending with the empty think block), which is what the evaluator feeds to ``generate``.
    """
    assistant_positions = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    last_assistant = assistant_positions[-1] if assistant_positions else None
    out = []
    for index, message in enumerate(messages):
        role = message["role"]
        if role == "system":
            out.append(f"{IM_START}system\n{render_text(message).strip()}{IM_END}\n")
        elif role == "user":
            out.append(f"{IM_START}user\n{render_text(message).strip()}{IM_END}\n")
        elif role == "assistant":
            is_last = index == last_assistant
            if is_last and drop_final_assistant:
                out.append(f"{IM_START}assistant\n{EMPTY_THINK}")
                break
            think = EMPTY_THINK if (is_last and final_assistant_with_think) else ""
            out.append(f"{IM_START}assistant\n{think}{render_text(message).strip()}{IM_END}\n")
        else:
            raise ValueError(f"unexpected role {role}")
    text = "".join(out)
    if not drop_final_assistant and text.endswith("\n"):
        text = text[:-1]  # train up to and including the final <|im_end|>
    return text


def trajectory_messages(record: dict[str, Any], variant: dict[str, bool]) -> list[dict[str, Any]]:
    return build_trajectory_messages(record, **variant)


def supervised_spans(input_ids: torch.Tensor, tokenizer: Any) -> list[tuple[int, int]]:
    """Return ``[(start, end_inclusive)]`` token spans covering each assistant completion + <|im_end|>."""
    ids = input_ids.tolist()
    header = tokenizer(f"{IM_START}assistant\n", add_special_tokens=False)["input_ids"]
    think = tokenizer(EMPTY_THINK, add_special_tokens=False)["input_ids"]
    im_end = tokenizer.convert_tokens_to_ids(IM_END)
    spans = []
    i = 0
    n = len(ids)
    while i < n:
        if ids[i : i + len(header)] == header:
            start = i + len(header)
            if ids[start : start + len(think)] == think:
                start += len(think)
            end = start
            while end < n and ids[end] != im_end:
                end += 1
            if end < n:
                spans.append((start, end))
                i = end + 1
                continue
            break
        i += 1
    return spans


def labels_from_spans(input_ids: torch.Tensor, spans: list[tuple[int, int]]) -> torch.Tensor:
    labels = torch.full_like(input_ids, IGNORE_INDEX)
    for start, end in spans:
        labels[start : end + 1] = input_ids[start : end + 1]
    return labels
