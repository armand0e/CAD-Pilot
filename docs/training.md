# Training the Qwen3.5-9B CAD visual-action policy

This is the operational companion to `TRAINING_HANDOFF_QWEN35_9B.md`. Every command below is run
from the repository root. Two virtual environments are involved:

| Purpose | Interpreter |
|---|---|
| data pipeline (`cad1000` CLI, stdlib only) | `.venv/bin/python` |
| training / evaluation (torch, transformers, peft) | `/home/armand0e/Documents/training/.venv/bin/python` (`$TRAIN_PY` below) |

```bash
export TRAIN_PY=/home/armand0e/Documents/training/.venv/bin/python
```

## 1. Build the corpus incrementally (storage-bounded, restartable)

```bash
# Round-robin over all ten applications; each workflow is downloaded, prepared, validated,
# check-summed into data/shards/<software>/<workflow-id>/, and only then its clip.mp4/events.json
# are deleted. Completed shards are skipped on restart. Stops when free disk < --min-free-gib.
.venv/bin/cad1000 build-shards --shard-root data/shards --min-free-gib 40

# Balanced subset for the pilot (e.g. 20 workflows per application), 3D-first:
.venv/bin/cad1000 build-shards --software solidworks --software nx-cad --software catia \
  --software sketchup --software revit-architecture --software autocad \
  --max-workflows-per-software 20

# Concatenate validated shards (frame paths are rewritten relative to the output directory).
.venv/bin/cad1000 merge-shards --shard-root data/shards --output data/processed/corpus-v1
```

Typed values are never split across narration spans (typing groups are formed over the whole
event stream first), and `merge-shards` repairs already-split values in older shards — 1,220 of
27,670 rows in the first pilot set needed this. Navigation steps (orbit, zoom, scroll) are kept:
inspecting the model is part of the behaviour to learn.

Each shard manifest records the pinned revision, source file OIDs/sizes, prepare parameters,
counts, JSONL SHA-256 digests and a combined frame digest. `merge-shards` re-verifies the JSONL
digests before concatenation.

## 2. Derive the policy view

```bash
.venv/bin/cad1000 policy-view --input data/processed/corpus-v1/all.jsonl \
  --output data/processed/policy-v1 --max-chunk 1 --drag-mode exclude
.venv/bin/cad1000 validate --policy data/processed/policy-v1/all.jsonl
```

The policy row (`cad-policy-1.0`) holds `prompt` (system + user with one image placeholder),
`completion` (canonical strict JSON), `actions`, `screen`, `software`, and an `audit` block. The
narration's `action_summary` lives only in `audit` and the validator fails if it appears in the
prompt. Coordinates are integers on a `0..999` grid (`round(v * 999)`; runtime inverse
`g / 999 * screen_px`, round-trip error <= half a pixel).

Chunking: `--max-chunk 1` (baseline) emits the first executable action; `--max-chunk 4` emits up
to four actions but cuts at pauses longer than `--max-gap-s` (default 1 s). Consecutive
printable keystrokes are always one `type_text`. Bare modifier presses are dropped. Drags are
reconstructed from press → drag-samples → release gestures (`cad-vla-1.1`); a gesture moving less
than 4 px stays a click. `--drag-mode include` enables drag targets once the runtime executes
`{"type":"drag","x","y","end_x","end_y","button"}`.

Key names are derived from the recorder's virtual-key codes per `metadata.platform`
(`windows` VK codes verified: Enter=13, Escape=27, numpad digits 96–105; `mac` table included).
Modifier bit `ctrl=2` is verified from Ctrl+Z events; `shift=1, alt=4, meta=8` are inferred and
flagged in every policy report.

## 2b. Long-context trajectory view (65K)

```bash
.venv/bin/cad1000 trajectory-view --input data/processed/corpus-v1/all.jsonl \
  --output data/processed/trajectory-v1-64k --budget-tokens 61440
.venv/bin/cad1000 validate --trajectory data/processed/trajectory-v1-64k/all.jsonl
```

A `cad-trajectory-1.0` record is one window of consecutive accepted steps of a workflow: a text
log of the steps before the window (`N. intent -> {"actions":...}`, most recent lines kept within
20 % of the budget, older ones counted as omitted), then each step as a user turn (step number,
intent, screenshot) and an assistant turn (strict JSON). Windows are consecutive and
non-overlapping so every step is supervised once per epoch; ~50 steps fit in a 61K window at
880 tokens per 1280×720 frame. The trainer computes loss on every assistant turn
(`cad_policy/trajectory.py` finds the spans; rendering is byte-identical to Qwen3.5's chat
template — earlier assistant turns without a think block, the generated turn with the empty
`<think>` block). The evaluator scores the last step of each window with the full history in
context. This layout is the runtime contract for long sessions: keep the full action log as text,
keep screenshots for recent steps, drop the oldest screenshots first.

Memory: at 65K tokens dense logits would need >32 GiB, so `cad_policy/loss.py` projects only the
supervised positions through `lm_head` (`sparse_lm_loss`, unit-tested equal to dense CE).
Measured on the CMP 170HX: 55–56K-token windows take ~39 s per forward/backward at peak
58.2 GiB (bf16 LoRA, checkpointing, causal-conv1d + fla kernels). Keep the converter budget at
61,440 for headroom; use `qlora_nf4.yaml` settings if a run OOMs.

```bash
$TRAIN_PY training/preflight.py --config training/configs/history64k.yaml
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/history64k.yaml
$TRAIN_PY training/evaluate.py --config training/configs/history64k.yaml --adapter runs/history64k/adapter \
  --split validation.jsonl --output runs/eval/history64k
```

## 3. Token / image-token report

```bash
$TRAIN_PY training/token_report.py --config training/configs/lora_bf16.yaml
```

Image tokens are predicted from the image size with the processor's own resize rule
(32 px per token after 2×2 merge; a 1280×720 frame becomes 1280×704 → 880 tokens) and the
prediction is cross-checked against the real processor in `preflight.py` and the unit tests. If a
record exceeds `data.max_total_tokens` (8192) the prompt is shortened deterministically — first
the rubric requirements are dropped, then the description — and records that still do not fit
are excluded and counted. No token-level truncation ever touches image tokens.

## 4. Preflight (required before any run)

```bash
$TRAIN_PY training/preflight.py --config training/configs/lora_bf16.yaml
```

Prints the token report, the exact LoRA target list (language decoder only: 8×4 attention +
24×3 Gated-DeltaNet + 32×3 MLP projections = 200 modules), the trainable-parameter percentage,
the label-mask audit (supervised text must be exactly the JSON completion plus `<|im_end|>`, zero
supervised image tokens), then runs forward/backward on real batches and reports loss, step time
and peak VRAM. It fails hard on any estimate mismatch.

## 5. Rollout sequence

```bash
# 1. base-model zero-shot on held-out records
$TRAIN_PY training/evaluate.py --config training/configs/lora_bf16.yaml \
  --split validation.jsonl --output runs/eval/base
# 2. 32-example overfit (loss must fall towards ~0)
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/overfit32.yaml
# 3. 256-example smoke run, then read runs/smoke256/train_summary.json and the predictions
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/smoke256.yaml
$TRAIN_PY training/evaluate.py --config training/configs/smoke256.yaml \
  --adapter runs/smoke256/adapter --output runs/eval/smoke256 --baseline-metrics runs/eval/base/metrics.json
# 4. balanced pilot
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/pilot8k.yaml
# 5. full run (only after 1-4 look right; test.jsonl stays untouched until then)
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/lora_bf16.yaml
```

Resume an interrupted run from its last checkpoint:

```bash
$TRAIN_PY training/train_qwen35_cad.py --config training/configs/lora_bf16.yaml --resume
```

If BF16 LoRA OOMs after `per_device_batch: 1` and checkpointing, switch to
`training/configs/qlora_nf4.yaml` (NF4 + double quantisation, bf16 compute, vision tower kept in
bf16). Prefer that over lowering `data.image.max_pixels`.

Every run directory contains `config.resolved.json`, `run_manifest.json` (package versions,
source-file hashes, data hashes, GPU, seed), `trainable_parameters.json`,
`label_mask_audit.json`, `token_report.json`, checkpoints, `adapter/` (adapter + processor +
chat template) and `train_summary.json` (loss curve, elapsed time, peak VRAM).

## 6. Evaluation outputs

`evaluate.py` writes `predictions.jsonl` and `metrics.json` with: strict JSON parse rate,
schema-valid rate, first-action type accuracy, sequence type accuracy, exact match, key/modifier
and `type_text` accuracy, click error in normalized units and screen pixels, hit rate within
8/16/32/64 px, all per application plus an unweighted macro average, and (with
`--baseline-metrics`) the delta versus the base model on the same records. Generation is greedy
with `enable_thinking=False` (the prompt already ends with an empty `<think></think>` block).

## 7. Merge / serve

```bash
$TRAIN_PY training/merge_adapter.py --config training/configs/lora_bf16.yaml \
  --adapter runs/lora-bf16/adapter --output runs/lora-bf16/merged
# merged:   vllm serve runs/lora-bf16/merged --served-model-name cad-policy --default-chat-template-kwargs '{"enable_thinking": false}'
# adapter:  vllm serve Qwen/Qwen3.5-9B --enable-lora --lora-modules cad-policy=runs/lora-bf16/adapter --max-lora-rank 32
```

The runtime must send the same system prompt and user layout as `cad1000.policy_view.build_prompt_messages`
and convert grid coordinates back with `grid_to_pixels`.

## 8. Tests

```bash
.venv/bin/pytest -q                                   # data pipeline: drags, keys, policy schema, shards restart/cleanup, leakage
$TRAIN_PY -m pytest training/tests -q                 # config, metrics, LoRA targets vs real architecture, label mask vs real processor
```

## 9. Environment notes

* The GPU (CMP 170HX, 64 GiB) is shared with a vLLM server (`qwen3.8-27b`, port 8000) that holds
  ~61 GiB while running. Stop it before preflight/training.
* `flash-linear-attention` 0.5.2 and `causal-conv1d` 1.7.0 are installed in the training venv, so
  transformers reports the Gated-DeltaNet fast path available. `causal-conv1d` was built from
  source with the pip `nvidia-cuda-nvcc==13.0.*` toolchain
  (`CUDA_HOME=<venv>/lib/python3.12/site-packages/nvidia/cu13`, plus a `libcudart.so` symlink in
  its `lib/`); nvcc must match torch's CUDA minor (13.0).
* Restart the user's inference endpoint after GPU work: `bash /home/armand0e/Documents/vllm/restart_qwen38_endpoint.sh`.
* `unsloth` and `flash-attn` are not installed; attention uses SDPA.
* Host RAM is 32 GiB: merge adapters with `--device cuda` when the GPU is free.
