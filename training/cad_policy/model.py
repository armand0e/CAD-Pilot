"""Model/processor loading and explicit LoRA targeting for Qwen3.5-9B."""

from __future__ import annotations

import re
from typing import Any

import torch

# Qwen3.5-9B language decoder: 24 Gated-DeltaNet layers (linear_attn.*) + 8 full-attention layers
# (self_attn.*), each followed by a SwiGLU MLP. ``in_proj_a``/``in_proj_b`` are tiny gate
# projections (4096 -> 32); a rank-32 adapter there is larger than the layer, so they are opt-in.
LANGUAGE_ATTENTION = ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj")
LANGUAGE_LINEAR_ATTENTION = ("linear_attn.in_proj_qkv", "linear_attn.in_proj_z", "linear_attn.out_proj")
LANGUAGE_LINEAR_ATTENTION_GATES = ("linear_attn.in_proj_a", "linear_attn.in_proj_b")
LANGUAGE_MLP = ("mlp.gate_proj", "mlp.up_proj", "mlp.down_proj")
MERGER = ("model.visual.merger.linear_fc1", "model.visual.merger.linear_fc2")
LANGUAGE_PREFIX = r"model\.language_model\.layers\.\d+\."


def lora_target_regex(lora_config: dict[str, Any]) -> str:
    preset = lora_config.get("targets", "language_decoder")
    if preset != "language_decoder":
        raise ValueError(f"unknown LoRA target preset {preset!r}")
    names = list(LANGUAGE_ATTENTION + LANGUAGE_LINEAR_ATTENTION + LANGUAGE_MLP)
    if lora_config.get("include_linear_attn_gates"):
        names.extend(LANGUAGE_LINEAR_ATTENTION_GATES)
    alternatives = "|".join(re.escape(name) for name in names)
    pattern = f"{LANGUAGE_PREFIX}(?:{alternatives})"
    if lora_config.get("include_merger"):
        merger = "|".join(re.escape(name) for name in MERGER)
        pattern = f"(?:{pattern})|(?:{merger})"
    return pattern


def matched_modules(model: torch.nn.Module, pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [name for name, module in model.named_modules() if isinstance(module, torch.nn.Linear) and regex.fullmatch(name)]


def build_lora_config(lora_config: dict[str, Any]) -> Any:
    from peft import LoraConfig

    return LoraConfig(
        r=int(lora_config["r"]),
        lora_alpha=int(lora_config["alpha"]),
        lora_dropout=float(lora_config["dropout"]),
        bias=lora_config.get("bias", "none"),
        target_modules=lora_target_regex(lora_config),
        task_type="CAUSAL_LM",
    )


def load_processor(model_config: dict[str, Any]) -> Any:
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(model_config["name"], revision=model_config.get("revision"))
    tokenizer = processor.tokenizer
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return processor


def load_base_model(model_config: dict[str, Any], *, device_map: Any = {"": 0}, training: bool = True) -> Any:
    from transformers import Qwen3_5ForConditionalGeneration

    dtype = getattr(torch, model_config.get("dtype", "bfloat16"))
    kwargs: dict[str, Any] = {
        "dtype": dtype,
        "attn_implementation": model_config.get("attn_implementation", "sdpa"),
        "device_map": device_map,
        "revision": model_config.get("revision"),
    }
    quantization = model_config.get("quantization", "none")
    if quantization == "nf4":
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype,
            # Keep the vision tower and lm_head in bf16; only the language decoder is quantized.
            llm_int8_skip_modules=["visual", "lm_head"],
        )
    elif quantization != "none":
        raise ValueError(f"unknown quantization {quantization!r}")
    model = Qwen3_5ForConditionalGeneration.from_pretrained(model_config["name"], **kwargs)
    if training:
        model.config.use_cache = False
    return model


def attach_lora(model: Any, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Freeze everything, attach LoRA to the language decoder, return the trainable summary."""
    from peft import get_peft_model, prepare_model_for_kbit_training

    lora_config = config["lora"]
    train_config = config["train"]
    quantized = config["model"].get("quantization", "none") != "none"
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    if quantized:
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=bool(train_config["gradient_checkpointing"]),
            gradient_checkpointing_kwargs={"use_reentrant": False},
        )
    pattern = lora_target_regex(lora_config)
    targets = matched_modules(model, pattern)
    if not targets:
        raise RuntimeError(f"LoRA pattern matched no modules: {pattern}")
    if any(".visual." in name for name in targets) and not lora_config.get("include_merger"):
        raise RuntimeError("LoRA pattern unexpectedly matched the vision tower")
    model = get_peft_model(model, build_lora_config(lora_config))
    if lora_config.get("train_merger_full"):
        for name, parameter in model.named_parameters():
            if ".visual.merger." in name and "lora_" not in name:
                parameter.requires_grad_(True)
    if train_config["gradient_checkpointing"]:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    summary = trainable_summary(model)
    summary["lora_target_regex"] = pattern
    summary["lora_target_modules"] = targets
    return model, summary


def trainable_summary(model: torch.nn.Module) -> dict[str, Any]:
    trainable = [(name, parameter.numel()) for name, parameter in model.named_parameters() if parameter.requires_grad]
    total = sum(parameter.numel() for parameter in model.parameters())
    trainable_count = sum(count for _, count in trainable)
    vision_trainable = sum(count for name, count in trainable if ".visual." in name)
    return {
        "total_parameters": total,
        "trainable_parameters": trainable_count,
        "trainable_percent": round(100.0 * trainable_count / max(total, 1), 4),
        "vision_trainable_parameters": vision_trainable,
        "trainable_parameter_names": [name for name, _ in trainable],
    }


def peak_vram_gib() -> float:
    return torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
