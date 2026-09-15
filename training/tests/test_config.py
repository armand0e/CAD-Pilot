from __future__ import annotations

from pathlib import Path

from cad_policy.config import DEFAULTS, load_config

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_defaults_and_extends(tmp_path: Path) -> None:
    base = load_config(CONFIGS / "lora_bf16.yaml")
    assert base["lora"]["r"] == 32 and base["model"]["quantization"] == "none"
    assert base["train"]["grad_accum"] == 32 and base["data"]["max_total_tokens"] == 8192
    child = load_config(CONFIGS / "qlora_nf4.yaml")
    assert child["model"]["quantization"] == "nf4"
    assert child["model"]["name"] == base["model"]["name"]
    assert child["lora"] == base["lora"]
    assert len(child["_meta"]["sources"]) == 2
    overfit = load_config(CONFIGS / "overfit32.yaml")
    assert overfit["data"]["max_examples"] == 32 and overfit["data"]["image"] == DEFAULTS["data"]["image"]
