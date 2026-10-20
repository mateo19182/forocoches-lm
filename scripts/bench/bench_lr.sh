#!/usr/bin/env bash
# Short learning-rate / grad-clip sweep on a 34M-token slice of the v1 mix (data/mix/sweep), plus the
# calidad-conditioning test of v1. Needs the GPU to itself:
#   scripts/gpu_train.sh scripts/bench/bench_lr.sh [steps]
# Each variant trains STEPS optimizer steps and evaluates once at the end; compare eval_loss.
set -uo pipefail
STEPS=${1:-150}; shift || true
cd "$(dirname "$0")/.."
mkdir -p runs/sweep samples

# Whole-thread perplexity (samples/cond_v1.md) did not move with the header: replies dominate it. Score only the
# title + opener start, where the header could matter.
echo "=== conditioning test (v1, historia val docs with the calidad header swapped, first 256 completion tokens)"
.venv-train/bin/python scripts/eval_ppl.py --val data/mix/cond/val.jsonl --models v1=runs/v1/final --first 256 \
  --out samples/cond_v1_first256.md > runs/sweep/cond256.log 2>&1 && tail -n +3 samples/cond_v1_first256.md

run() {  # name lr clip
  echo "=== $1  lr=$2 clip=$3"
  .venv-train/bin/python -u train.py --data data/mix/sweep --out "runs/sweep/$1" --max-steps "$STEPS" \
    --eval-steps "$STEPS" --warmup-ratio 0.1 --workers 0 --lr "$2" --max-grad-norm "$3" \
    > "runs/sweep/$1.log" 2>&1
  tr '\r' '\n' < "runs/sweep/$1.log" | grep -oE "\{'loss'[^}]*\}|\{'eval_loss'[^}]*\}" \
    | python3 -c "
import sys, ast
rows = [ast.literal_eval(l) for l in sys.stdin]
tr = [r for r in rows if 'loss' in r]; ev = [r for r in rows if 'eval_loss' in r]
gn = [float(r['grad_norm']) for r in tr]
print(f'  train loss first/last: {tr[0][\"loss\"]} / {tr[-1][\"loss\"]}   grad_norm median {sorted(gn)[len(gn)//2]:.2f} max {max(gn):.2f}')
print(f'  eval_loss: {ev[-1][\"eval_loss\"] if ev else \"none\"}')" 2>/dev/null || echo "  no metrics (see runs/sweep/$1.log)"
  tr '\r' '\n' < "runs/sweep/$1.log" | grep -E "Traceback|Error" | grep -v Warning | head -2
  rm -rf "runs/sweep/$1/checkpoint-"*
}

run lr2e-5_clip1  2e-5 1.0
run lr2e-5_clip10 2e-5 10
run lr5e-5_clip10 5e-5 10
run lr1e-4_clip10 1e-4 10
