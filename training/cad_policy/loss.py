"""Sparse-logit causal LM loss: project only supervised positions through ``lm_head``.

At 65K tokens the dense logits tensor (seq x 248,320 vocab) would need ~32 GiB in bf16 and twice
that in fp32; only a few percent of positions carry loss, so we run the decoder for hidden states
and apply the output projection to those positions alone. Numerically identical to the standard
mean cross-entropy over supervised tokens.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F

IGNORE_INDEX = -100


def unwrap_causal_lm(model: Any) -> Any:
    """Return the underlying causal LM (e.g. ``Qwen3_5ForConditionalGeneration``) behind PEFT wrappers.

    Uses registered submodules only: PEFT wrappers forward attribute access to the wrapped model,
    so ``hasattr`` checks would stop one level too early and call the dense-logit forward.
    """
    current = model
    for _ in range(6):
        modules = getattr(current, "_modules", {})
        if "lm_head" in modules and "model" in modules:
            return current
        if "base_model" in modules:
            current = modules["base_model"]
        elif "model" in modules:
            current = modules["model"]
        else:
            break
    raise TypeError(f"cannot find causal LM head in {type(model).__name__}")


def sparse_lm_loss(model: Any, inputs: dict[str, torch.Tensor], *, num_items_in_batch: int | None = None) -> torch.Tensor:
    inputs = dict(inputs)
    labels = inputs.pop("labels")
    causal_lm = unwrap_causal_lm(model)
    outputs = causal_lm._modules["model"](**inputs, use_cache=False, return_dict=True)
    hidden = outputs.last_hidden_state
    shift_labels = labels[:, 1:]
    mask = shift_labels != IGNORE_INDEX
    selected = hidden[:, :-1][mask]
    if selected.numel() == 0:
        return hidden.sum() * 0.0
    logits = causal_lm._modules["lm_head"](selected).float()
    targets = shift_labels[mask]
    if num_items_in_batch is not None:
        return F.cross_entropy(logits, targets, reduction="sum") / num_items_in_batch
    return F.cross_entropy(logits, targets)
