#!/usr/bin/env bash
# Restartable full corpus run. Launch with nohup; never equate exit with success.
set -Eeuo pipefail
cd /home/armand0e/Documents/cad-model
RUN=runs/full-run
mkdir -p "$RUN"
exec 9>"$RUN/chain.lock"
flock -n 9 || exit 0
if [ -f "$RUN/quality-hold.md" ]; then
  echo 'Paused for the approved v3 corpus repair. See runs/full-run/quality-hold.md.'
  exit 0
fi
TRAIN_PY=/home/armand0e/Documents/training/.venv/bin/python
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PYTHONUNBUFFERED=1
# The base model + processor are fully cached; systemd's clean env has no HF_HOME/HF_TOKEN and
# Qwen/Qwen3.5-9B is gated, so resolve it from the local cache offline.
export HF_HOME=/home/armand0e/.cache/huggingface
export HF_HUB_OFFLINE=1
ENDPOINT_STOPPED=0
if [ -f "$RUN/endpoint-restore-needed" ]; then ENDPOINT_STOPPED=1; fi
status() { date '+%Y-%m-%d %H:%M:%S %Z' | tr '\n' ' '; echo "$*"; }
restore_endpoint() {
  if [ "$ENDPOINT_STOPPED" -eq 1 ]; then
    # Give the restored endpoint its own service so it outlives this completed chain.
    if ! curl --max-time 3 -fsS http://127.0.0.1:8000/health >/dev/null; then
      systemd-run --user --collect --property=Type=forking /bin/bash /home/armand0e/Documents/vllm/restart_qwen38_endpoint.sh
    fi
    for ((n=0; n<60; n++)); do
      if curl --max-time 3 -fsS http://127.0.0.1:8000/health >/dev/null; then
        ENDPOINT_STOPPED=0
        mv "$RUN/endpoint-restore-needed" "$RUN/endpoint-restored.ok"
        status 'Endpoint restored'; return 0
      fi
      sleep 10
    done
    status 'ERROR: endpoint did not become healthy'; return 1
  fi
}
finish() {
  rc=$?
  trap - EXIT
  restore_endpoint || rc=1
  if [ "$rc" -ne 0 ]; then status "FAILED (exit $rc); inspect stage logs"; fi
  exit "$rc"
}
trap finish EXIT
stage() {
  local name=$1; shift
  if [ -f "$RUN/$name.ok" ]; then return; fi
  status "Starting $name"
  "$@" >"$RUN/$name.log" 2>&1
  touch "$RUN/$name.ok"
  status "Completed $name"
}

status 'Waiting for existing shard builders'
while pgrep -f '^/home/armand0e/Documents/cad-model/.venv/bin/python .venv/bin/cad1000 build-shards' >/dev/null; do sleep 30; done
# Skips validated shards; retries missing and failed workflows without touching active builders.
if [ ! -f "$RUN/corpus-audit.ok" ]; then
  for ((attempt=1; attempt<=4; attempt++)); do
    status "Corpus retry pass $attempt"
    .venv/bin/cad1000 build-shards --shard-root data/shards --raw-root data/raw-full-retry --min-free-gib 100 >"$RUN/build-$attempt.log" 2>&1 || true
    if .venv/bin/python scripts/audit_full_corpus.py >"$RUN/corpus-audit.log" 2>&1; then
      touch "$RUN/corpus-audit.ok"; break
    fi
    sleep 30
  done
  test -f "$RUN/corpus-audit.ok"
fi
stage merge .venv/bin/cad1000 merge-shards --shard-root data/shards --output data/processed/corpus-v2
stage policy .venv/bin/cad1000 policy-view --input data/processed/corpus-v2/all.jsonl --output data/processed/policy-v2
stage policy-validation .venv/bin/cad1000 validate --policy data/processed/policy-v2/all.jsonl
stage trajectory .venv/bin/cad1000 trajectory-view --input data/processed/corpus-v2/all.jsonl --output data/processed/trajectory-v2-64k --budget-tokens 61440
stage trajectory-validation .venv/bin/cad1000 validate --trajectory data/processed/trajectory-v2-64k/all.jsonl

status 'Releasing the inference GPU for full training and evaluation'
mapfile -t endpoint_pids < <(pgrep -f '^/home/armand0e/Documents/vllm/.venv/bin/python /home/armand0e/Documents/vllm/.venv/bin/vllm serve .*--served-model-name qwen3.8-27b' || true)
if [ "${#endpoint_pids[@]}" -gt 0 ]; then
  ENDPOINT_STOPPED=1
  touch "$RUN/endpoint-restore-needed"
  kill -TERM "${endpoint_pids[@]}"
fi
for ((n=0; n<60; n++)); do
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
  [ "$used" -lt 2000 ] && break
  sleep 5
done
test "$used" -lt 2000

if [ ! -f "$RUN/train.ok" ]; then
  for ((attempt=1; attempt<=4; attempt++)); do
    resume=()
    compgen -G 'runs/cad-policy-v1-64k/checkpoint-*/trainer_state.json' >/dev/null && resume=(--resume)
    status "Training attempt $attempt ${resume[*]}"
    if "$TRAIN_PY" training/train_qwen35_cad.py --config training/configs/full64k.yaml "${resume[@]}" >"$RUN/train-$attempt.log" 2>&1; then
      test -s runs/cad-policy-v1-64k/train_summary.json
      test -s runs/cad-policy-v1-64k/adapter/adapter_model.safetensors
      touch "$RUN/train.ok"; break
    fi
    sleep 30
  done
  test -f "$RUN/train.ok"
fi
stage base-eval "$TRAIN_PY" training/evaluate.py --config training/configs/full64k.yaml --split validation.jsonl --output runs/eval/base-full64k-val
stage adapter-eval "$TRAIN_PY" training/evaluate.py --config training/configs/full64k.yaml --adapter runs/cad-policy-v1-64k/adapter --split validation.jsonl --output runs/eval/cad-policy-v1-64k-val --baseline-metrics runs/eval/base-full64k-val/metrics.json
stage report "$TRAIN_PY" training/report_run.py --run runs/cad-policy-v1-64k --eval runs/eval/cad-policy-v1-64k-val --baseline runs/eval/base-full64k-val
test -s runs/eval/base-full64k-val/metrics.json
test -s runs/eval/cad-policy-v1-64k-val/metrics.json
test -s runs/cad-policy-v1-64k/report.md
restore_endpoint
touch "$RUN/complete.ok"
status 'COMPLETE: full corpus, training, both evaluations, report, and endpoint restoration verified'
