from __future__ import annotations

import re

import pytest
import torch

from cad_policy.model import lora_target_regex, matched_modules


def test_regex_covers_language_decoder_only() -> None:
    pattern = re.compile(lora_target_regex({"targets": "language_decoder"}))
    assert pattern.fullmatch("model.language_model.layers.3.self_attn.q_proj")
    assert pattern.fullmatch("model.language_model.layers.0.linear_attn.in_proj_qkv")
    assert pattern.fullmatch("model.language_model.layers.31.mlp.down_proj")
    assert not pattern.fullmatch("model.language_model.layers.0.linear_attn.in_proj_a")
    assert not pattern.fullmatch("model.visual.blocks.0.attn.qkv")
    assert not pattern.fullmatch("model.visual.merger.linear_fc1")
    assert not pattern.fullmatch("lm_head")
    gated = re.compile(lora_target_regex({"targets": "language_decoder", "include_linear_attn_gates": True}))
    assert gated.fullmatch("model.language_model.layers.0.linear_attn.in_proj_b")
    merger = re.compile(lora_target_regex({"targets": "language_decoder", "include_merger": True}))
    assert merger.fullmatch("model.visual.merger.linear_fc2") and merger.fullmatch("model.language_model.layers.1.mlp.up_proj")


def test_regex_against_real_architecture_on_meta_device() -> None:
    transformers = pytest.importorskip("transformers")
    try:
        config = transformers.AutoConfig.from_pretrained("Qwen/Qwen3.5-9B", local_files_only=True)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Qwen3.5-9B config not cached: {error}")
    with torch.device("meta"):
        model = transformers.Qwen3_5ForConditionalGeneration(config)
    names = matched_modules(model, lora_target_regex({"targets": "language_decoder"}))
    # 8 full-attention layers x 4 + 24 linear-attention layers x 3 + 32 MLP layers x 3
    assert len(names) == 8 * 4 + 24 * 3 + 32 * 3
    assert not any(".visual." in name for name in names)
